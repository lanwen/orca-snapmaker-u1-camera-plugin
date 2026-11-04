"""Review prepared print states in an isolated Chrome session, without a server."""

import argparse
import base64
from contextlib import contextmanager
import html
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request

import websocket

from prepare_screenshots import PREVIEW_LABELS, ROOT, find_browser


class DevTools:
    def __init__(self, connection):
        self.connection = connection
        self.request_id = 0

    def call(self, method, params=None):
        self.request_id += 1
        self.connection.send(json.dumps({"id": self.request_id, "method": method, "params": params or {}}))
        while True:
            reply = json.loads(self.connection.recv())
            if reply.get("id") == self.request_id:
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                return reply.get("result", {})

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"])
        return result["result"].get("value")

    def wait_for(self, expression):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(.05)
        raise RuntimeError("Preview did not become ready: " + expression)

    def screenshot(self, path):
        clip = self.evaluate("""(() => {
          const r = document.querySelector('#stats').getBoundingClientRect();
          return {x:r.x, y:r.y, width:r.width, height:r.height, scale:1};
        })()""")
        data = self.call("Page.captureScreenshot", {"format": "png", "clip": clip})["data"]
        Path(path).write_bytes(base64.b64decode(data))


@contextmanager
def browser():
    with tempfile.TemporaryDirectory(prefix="u1-preview-browser-") as profile, tempfile.TemporaryFile() as log:
        process = subprocess.Popen([
            find_browser(), "--headless", "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--disable-sync", "--hide-scrollbars",
            "--remote-debugging-port=0", "--user-data-dir=" + profile, "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=log)
        connection = None
        try:
            port_file = Path(profile) / "DevToolsActivePort"
            deadline = time.monotonic() + 10
            while not port_file.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    log.seek(0)
                    raise RuntimeError("Chrome failed to start: " + log.read().decode(errors="replace")[-2000:])
                time.sleep(.05)
            port = int(port_file.read_text().splitlines()[0])
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=10) as response:
                tabs = json.load(response)
            target = next(tab for tab in tabs if tab["type"] == "page" and tab["url"] == "about:blank")
            connection = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10,
                                                     suppress_origin=True, http_no_proxy=["*"])
            yield DevTools(connection)
        finally:
            if connection:
                connection.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def seek(client, milliseconds):
    client.evaluate(f"document.getAnimations().forEach(a => {{a.pause(); a.currentTime={milliseconds}}})")


def motion_style(client):
    return client.evaluate("""(() => {
      const fill = document.querySelector('#progress-fill');
      const s = getComputedStyle(fill, '::after');
      return {animation:s.animationName, transform:s.transform, duration:parseFloat(s.animationDuration)*1000,
        gradient:s.backgroundImage, clipped:getComputedStyle(fill).overflow === 'hidden'};
    })()""")


def check_alignment(rows):
    for field, axes in (("bar", ("x", "y", "width")), ("estimate", ("y", "height")), ("status", ("y",))):
        visible = [row[field] for row in rows if row.get(field) and (field != "status" or row["bar"])]
        for axis in axes:
            if len({round(rect[axis], 1) for rect in visible}) > 1:
                raise ValueError(f"{field} {axis} differs between states")


def export_gif(client, destination, duration, ffmpeg):
    with tempfile.TemporaryDirectory(prefix="u1-preview-frames-") as directory:
        for frame in range(16):
            seek(client, frame * duration / 16)
            client.screenshot(Path(directory) / f"{frame:02d}.png")
        subprocess.run([
            ffmpeg, "-v", "error", "-y", "-framerate", str(16000 / duration),
            "-i", str(Path(directory) / "%02d.png"), "-filter_complex",
            "[0:v]split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse",
            "-loop", "0", str(destination),
        ], check=True, capture_output=True)


def review(directory, output, states, widths, gifs=False):
    entry = Path(directory).resolve() / "index.html"
    if not entry.is_file():
        raise ValueError("Prepare the preview first with scripts/prepare_screenshots.py")
    ffmpeg = shutil.which("ffmpeg") if gifs else None
    if gifs and not ffmpeg:
        raise ValueError("Install ffmpeg to export GIFs, or omit --gifs")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results, errors = [], []
    with browser() as client:
        client.call("Page.navigate", {"url": entry.as_uri()})
        client.wait_for("Boolean(document.querySelector('#preview-state') && document.querySelector('#stats'))")
        for width in widths:
            client.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900,
                                                               "deviceScaleFactor": 1, "mobile": False})
            rows = []
            for state in states:
                client.evaluate("""(async () => {
                  document.querySelector('[data-preview-state=' + CSS.escape(%s) + ']').click();
                  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                })()""" % json.dumps(state))
                row = client.evaluate("""(() => {
                  const rect = e => {
                    if (!e || !e.getClientRects().length) return null;
                    const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height};
                  };
                  return {bar:rect(document.querySelector('#progress-bar')),
                    status:rect(document.querySelector('#stats-state')),
                    estimate:rect([...document.querySelectorAll('#remaining-value,#plan-value')]
                      .find(e => e.getClientRects().length)),
                    active:['printing','paused'].includes(screenshotFixtures[screenshotState].state),
                    overflow:[...document.querySelectorAll('#stats,#plan-stat,#remaining-stat,#finish-value')]
                      .filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth+1).map(e => e.id)};
                })()""")
                row.update(state=state, width=width, screenshot=f"{state}-{width}.png")
                rows.append(row)
                results.append(row)
                try:
                    if row["overflow"]:
                        raise ValueError("Overflow: " + ", ".join(row["overflow"]))
                    style = motion_style(client)
                    if row["active"]:
                        if style["animation"] == "none" or not style["clipped"]:
                            raise ValueError("Active shimmer is missing or unclipped")
                        poses = []
                        for fraction in (0, .25, .5, .75):
                            seek(client, style["duration"] * fraction)
                            poses.append(motion_style(client)["transform"])
                        if len(set(poses)) < 3:
                            raise ValueError("Active shimmer does not move")
                        client.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
                        if motion_style(client)["animation"] != "none":
                            raise ValueError("Reduced motion still animates")
                    elif style["animation"] != "none":
                        raise ValueError("Inactive print still animates")
                    row["motion"] = "moving; reduced motion static" if row["active"] else "static"
                except ValueError as error:
                    errors.append(f"{state} at {width}px: {error}")
                finally:
                    client.call("Emulation.setEmulatedMedia", {"features": []})
                style = motion_style(client)
                if row["active"] and style["duration"]:
                    seek(client, style["duration"] / 2)
                    if gifs and width == widths[0]:
                        row["gif"] = f"{state}.gif"
                        export_gif(client, output / row["gif"], style["duration"], ffmpeg)
                client.screenshot(output / row["screenshot"])
            try:
                check_alignment(rows)
            except ValueError as error:
                errors.append(f"{width}px: {error}")
    report = {"states": states, "widths": widths, "results": results, "errors": errors}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    images = "".join(f'<figure><figcaption>{html.escape(row["state"])} · {row["width"]}px '
                     + (f'<a href="{row["gif"]}">Animation</a>' if "gif" in row else '') + '</figcaption>'
                     f'<img src="{row["screenshot"]}" alt="{html.escape(row["state"])}"></figure>' for row in results)
    (output / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>Print state review</title>'
        '<style>body{background:#10151b;color:#edf3f9;font:16px system-ui}figure{margin:24px}img{max-width:100%}</style>'
        '<h1>Print state review</h1><p>Offline sample data. Geometry and motion results: <a href="report.json">report.json</a>.</p>' + images)
    if errors:
        raise ValueError("Preview review failed:\n" + "\n".join(errors))
    return report


def positive_width(value):
    width = int(value)
    if width < 1:
        raise argparse.ArgumentTypeError("Width must be positive")
    return width


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, default=ROOT / "dist/screenshots")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/preview-review")
    parser.add_argument("--states", nargs="+", choices=PREVIEW_LABELS, default=list(PREVIEW_LABELS))
    parser.add_argument("--widths", nargs="+", type=positive_width, default=[1100, 390])
    parser.add_argument("--gifs", action="store_true", help="Export active-state GIFs at the first width; requires ffmpeg")
    args = parser.parse_args()
    try:
        report = review(args.preview, args.output, args.states, args.widths, args.gifs)
    except (ValueError, RuntimeError, OSError, websocket.WebSocketException, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
    print(f"Reviewed {len(report['states'])} states at {report['widths']}; alignment, overflow, and motion passed.")
    print(f"Review: {(args.output / 'index.html').resolve()}")
