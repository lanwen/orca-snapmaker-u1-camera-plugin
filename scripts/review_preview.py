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
          const stats = document.querySelector('#stats');
          const r = (stats.getClientRects().length ? stats : document.querySelector('.viewer')).getBoundingClientRect();
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
    client.evaluate(f"document.getAnimations().filter(a => a instanceof CSSAnimation).forEach(a => {{a.pause(); a.currentTime={milliseconds}}})")


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


def check_toolheads(client):
    result = client.evaluate("""(async () => {
      const wait = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const errors = [];
      const root = document.querySelector('#hardware-stat');
      const tabs = root.querySelectorAll('.index-tab');
      if (tabs.length !== 4) errors.push('Four toolhead tabs are required');
      const fixture = structuredClone(screenshotFixtures[screenshotState]);
      const generation = screenshotStates.indexOf(screenshotState) + 1;
      const update = async stats => { screenshotFixtures[screenshotState] = stats; screenshotHandler({generation, stats}); await wait(); };
      for (const id of [0, 1, 2, 3]) {
        const stats = structuredClone(fixture);
        stats.tools.forEach((tool, i) => { tool.state = i === id ? 'connected' : 'parked'; });
        stats.tools[id].heater = {temperature:157, target:210};
        await update(stats);
        if (root.querySelector('.chosen .index-tab')?.dataset.tool !== String(id)) errors.push('Wrong active tab');
        const trace = root.querySelector('.trace-draw');
        if (!trace || getComputedStyle(trace).animationName === 'none') errors.push('Tool change did not draw a path');
        const temperature = root.querySelector('.current-temperature').getBoundingClientRect();
        const origin = root.getBoundingClientRect();
        const end = trace.getPointAtLength(trace.getTotalLength() - 3);
        if (Math.abs(end.x - (temperature.right - origin.x + 4)) > 1 ||
            Math.abs(end.y - (temperature.y + temperature.height/2 - origin.y)) > 1) errors.push('Trace misses temperature');
        if (id === 0) {
          const nozzle = root.querySelector('.nozzle-size').getBoundingClientRect();
          if (Math.abs(nozzle.y + nozzle.height/2 - temperature.y - temperature.height/2) > 1) errors.push('T0 route is not horizontal');
        }
        const before = trace.getAttribute('d');
        stats.tools[id].heater = {temperature:9, target:0};
        await update(stats);
        if (before === root.querySelector('.trace-draw').getAttribute('d')) errors.push('Temperature width did not update route');
        if (id === 1) {
          const tab = root.querySelector('.chosen .index-tab');
          tab.focus(); await wait();
          if (getComputedStyle(root.querySelector('[data-tool="0"] + .filament')).visibility !== 'visible') errors.push('Focus did not reveal parked filament');
          tab.click(); await wait();
          if (tab.getAttribute('aria-expanded') !== 'true') errors.push('Tap did not open tool details');
          tab.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true})); await wait();
          if (getComputedStyle(root.querySelector('.chosen .tool-tooltip')).visibility !== 'hidden') errors.push('Escape did not dismiss tool details');
          tab.blur();
        }
        stats.tools[id].state = 'changing';
        await update(stats);
        if (!root.querySelector('.chosen .head-state')?.textContent.includes('↻')) errors.push('Missing change indicator');
        if (root.querySelector('.trace-shimmer')) errors.push('Changing head is shown as connected');
      }
      await update({state:'unavailable'});
      if (root.getClientRects().length) errors.push('Offline hardware information is visible');
      screenshotFixtures[screenshotState] = fixture; publishScreenshot(); await wait();
      return errors;
    })()""")
    if result:
        raise ValueError('; '.join(result))


def check_tooltip_hover(client):
    tab = client.evaluate("(() => { const r=document.querySelector('.chosen .index-tab').getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()")
    client.call("Input.dispatchMouseEvent", {"type":"mouseMoved", **tab})
    client.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    position = client.evaluate("""(() => {
      const tooltip=document.querySelector('.chosen .tool-tooltip');
      const r=tooltip.getBoundingClientRect(), stats=document.querySelector('#stats').getBoundingClientRect();
      return {left:r.left, top:r.top, bottom:r.bottom, x:r.left+12, y:r.top+15,
        sectionTop:stats.top, cursor:getComputedStyle(document.querySelector('.chosen .index-tab')).cursor};
    })()""")
    if position["bottom"] > position["sectionTop"] - 7:
        raise ValueError("Tooltip overlaps the statistics section")
    if position["cursor"] == "pointer":
        raise ValueError("Toolhead advertises a clickable action")
    client.call("Input.dispatchMouseEvent", {"type":"mouseMoved", "x":position["x"], "y":position["bottom"]+4})
    client.evaluate("new Promise(resolve => setTimeout(resolve, 80))")
    client.call("Input.dispatchMouseEvent", {"type":"mouseMoved", "x":position["x"], "y":position["y"]})
    client.evaluate("new Promise(resolve => setTimeout(resolve, 300))")
    if not client.evaluate("getComputedStyle(document.querySelector('.chosen .tool-tooltip')).visibility === 'visible'"):
        raise ValueError("Tooltip closes while moving the pointer into it")
    client.call("Input.dispatchMouseEvent", {"type":"mousePressed", "button":"left", "clickCount":1, "x":position["x"], "y":position["y"]})
    for step in range(1, 6):
        client.call("Input.dispatchMouseEvent", {"type":"mouseMoved", "button":"left", "buttons":1, "x":position["x"]+step*20, "y":position["y"]})
        client.evaluate("new Promise(resolve => requestAnimationFrame(resolve))")
    client.call("Input.dispatchMouseEvent", {"type":"mouseReleased", "button":"left", "clickCount":1, "x":position["x"]+100, "y":position["y"]})
    if not client.evaluate("Boolean(getSelection().toString().trim())"):
        raise ValueError("Tooltip text cannot be selected")
    client.evaluate("getSelection().removeAllRanges()")
    client.call("Input.dispatchMouseEvent", {"type":"mouseMoved", "x":0, "y":0})
    client.evaluate("new Promise(resolve => setTimeout(resolve, 280))")


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
        client.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
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
                if state == "printing":
                    try:
                        check_toolheads(client)
                        check_tooltip_hover(client)
                    except ValueError as error:
                        errors.append(f"Toolheads at {width}px: {error}")
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
                    pill:rect(document.querySelector('#camera')),
                    dot:rect(document.querySelector('.stream-dot')),
                    headerStatus:rect(document.querySelector('#printer')),
                    address:rect(document.querySelector('#target')),
                    heading:rect(document.querySelector('h1')),
                    labelsMatch:!document.querySelector('#stats').getClientRects().length ||
                      document.querySelector('#printer').textContent === document.querySelector('#stats-state').textContent,
                    connecting:!document.querySelector('#empty').hidden,
                    lightError:document.querySelector('.light-area').dataset.error === 'true',
                    warning:!document.querySelector('#warning').hidden,
                    overflow:[...document.querySelectorAll('#stats,#plan-stat,#remaining-stat,#finish-value,#camera,.light-area,#warning-label,.printer-meta')]
                      .filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth+1).map(e => e.id)};
                })()""")
                if state in {"unavailable", "printer-unavailable"} and client.evaluate("Boolean(document.querySelector('#hardware-stat')?.getClientRects().length)"):
                    errors.append(f"{state} at {width}px: Hardware information is visible without status")
                if state in {"standby", "complete"} and client.evaluate("Boolean(document.querySelector('.index-stack')?.getClientRects().length || document.querySelector('.filament-traces')?.getClientRects().length)"):
                    errors.append(f"{state} at {width}px: Toolhead tabs are visible without a running print")
                row.update(state=state, width=width, screenshot=f"{state}-{width}.png")
                rows.append(row)
                results.append(row)
                try:
                    if row["overflow"]:
                        raise ValueError("Overflow: " + ", ".join(row["overflow"]))
                    header, address, heading = row["headerStatus"], row["address"], row["heading"]
                    if header and address:
                        if abs(header["y"] + header["height"] / 2 - address["y"] - address["height"] / 2) > 1:
                            raise ValueError("Header status is not beside the address")
                        if header["y"] < heading["y"] + heading["height"]:
                            raise ValueError("Header status is not below the title")
                    if not row["labelsMatch"]:
                        raise ValueError("Header and statistics status labels differ")
                    if row["connecting"]:
                        sweep = "getComputedStyle(document.querySelector('.connection-sweep'))"
                        if client.evaluate(sweep + ".animationName") == "none":
                            raise ValueError("Connecting lens does not animate")
                        seek(client, 0)
                        before = client.evaluate(sweep + ".transform")
                        seek(client, 700)
                        if before == client.evaluate(sweep + ".transform"):
                            raise ValueError("Connecting lens does not move")
                        client.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]})
                        if client.evaluate(sweep + ".animationName") != "none":
                            raise ValueError("Reduced motion still animates the connecting lens")
                        client.call("Emulation.setEmulatedMedia", {"features": []})
                    if row["pill"] and row["dot"]:
                        pill, dot = row["pill"], row["dot"]
                        if abs(pill["y"] + pill["height"] / 2 - dot["y"] - dot["height"] / 2) > 1:
                            raise ValueError("Stream dot is not vertically centered")
                        if pill["x"] + pill["width"] - dot["x"] - dot["width"] > 12:
                            raise ValueError("Stream dot is not on the right")
                    if row["warning"] != (state in {"permission-denied", "config-missing"}):
                        raise ValueError("Configuration warning visibility is incorrect")
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
                tab = "document.querySelector('.chosen .index-tab')"
                if client.evaluate(f"Boolean({tab}?.getClientRects().length)"):
                    client.evaluate(f"{tab}.focus()")
                    shown = client.evaluate("getComputedStyle(document.querySelector('.chosen .tool-tooltip')).visibility === 'visible'")
                    if not shown:
                        errors.append(f"{state} at {width}px: Toolhead details are not visible on focus")
                    tooltip = client.evaluate("(() => { const r=document.querySelector('.chosen .tool-tooltip').getBoundingClientRect(); return {left:r.left,right:r.right,top:r.top}; })()")
                    if tooltip["left"] < 0 or tooltip["right"] > width or tooltip["top"] < 0:
                        errors.append(f"{state} at {width}px: Toolhead details overflow the viewport")
                    row["profile"] = f"{state}-{width}-profile.png"
                    data = client.call("Page.captureScreenshot", {"format": "png"})["data"]
                    (output / row["profile"]).write_bytes(base64.b64decode(data))
                    client.evaluate(f"{tab}.blur()")
                client.screenshot(output / row["screenshot"])
                client.evaluate("document.querySelector('#stop').scrollIntoView({block:'nearest'})")
                stop = client.evaluate("(() => { const r=document.querySelector('#stop').getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()")
                client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **stop})
                client.evaluate("new Promise(resolve => setTimeout(resolve, 280))")
                row["viewport"] = f"{state}-{width}-viewport.png"
                data = client.call("Page.captureScreenshot", {"format": "png"})["data"]
                (output / row["viewport"]).write_bytes(base64.b64decode(data))
            try:
                check_alignment(rows)
            except ValueError as error:
                errors.append(f"{width}px: {error}")
    report = {"states": states, "widths": widths, "results": results, "errors": errors}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    images = "".join(f'<figure><figcaption>{html.escape(row["state"])} · {row["width"]}px '
                     + (f'<a href="{row["gif"]}">Animation</a>' if "gif" in row else '') + '</figcaption>'
                     f'<a href="{row["viewport"]}"><img src="{row["screenshot"]}" alt="{html.escape(row["state"])}"></a></figure>' for row in results)
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
