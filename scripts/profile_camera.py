"""Compare the full camera page in two standalone macOS WebKit processes."""

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import types

from profile_memory import positive, sample


ROOT = Path(__file__).resolve().parents[1]


def phase_at(seconds, cycle):
    position = seconds % cycle * 600 / cycle
    if position < 240:
        return "playing-first", "play"
    if position < 260:
        return "stopped-first", "stop"
    if position < 380:
        return "playing-second", "play"
    if position < 410:
        return "hidden", "hide"
    if position < 430:
        return "returned-stopped", "show"
    if position < 470:
        step = int((position - 430) / 2)
        return f"rapid-{step}", "play" if step % 2 == 0 else "stop"
    if position < 580:
        return "playing-third", "play"
    return "stopped-last", "stop"


def load_plugin():
    # Only the host registration types are stubbed; Printer and Monitor run unchanged.
    orca = types.ModuleType("orca")
    orca.pages = types.SimpleNamespace(PagesPluginCapabilityBase=object)
    orca.base = object
    orca.plugin = lambda cls: cls
    sys.modules["orca"] = orca
    sys.path.insert(0, str(ROOT / "src"))
    spec = importlib.util.spec_from_file_location("profiled_plugin", ROOT / "src/plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Player:
    def __init__(self, plugin, host, binary, page, variant, directory, mode):
        self.variant = variant
        self.host = host
        self.frames = self.bytes = self.requests = self.failures = 0
        self.acknowledgements = 0
        self.errors = deque(maxlen=20)
        self.ready = threading.Event()
        self.replies = queue.Queue(maxsize=2)
        self.write_lock = threading.Lock()
        self.request_id = 0
        self.identity = None
        self.pid = None
        self.last_frame_at = time.monotonic()

        player = self

        class CameraPrinter(plugin.Printer):
            def snapshot(self):
                player.requests += 1
                return super().snapshot()

            def set_light(self, command):
                raise RuntimeError("The profiling host does not allow light commands")

        self.monitor = plugin.Monitor(self.publish, CameraPrinter)
        self.log = (directory / (variant + "-native.log")).open("w")
        self.process = subprocess.Popen([str(binary), str(page), variant, mode], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
        self.reader = threading.Thread(target=self.receive, name=variant + " bridge", daemon=True)
        self.reader.start()

    def send(self, message):
        with self.write_lock:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()

    def publish(self, message):
        if "image" in message:
            self.frames += 1
            self.bytes += len(message["image"])
            self.last_frame_at = time.monotonic()
        if message.get("failed"):
            self.failures += 1
        self.send({"type": "deliver", "message": message})

    def receive(self):
        try:
            for line in self.process.stdout:
                message = json.loads(line)
                kind = message.get("type")
                if kind == "ready":
                    self.ready.set()
                elif kind == "activity":
                    self.monitor.update((self.host, "") if message.get("active") else None,
                                        playing=message.get("playing", False))
                elif kind == "frame":
                    self.acknowledgements += 1
                    self.monitor.frame_loaded(message.get("generation"))
                elif kind == "metrics":
                    self.replies.put_nowait(message)
                else:
                    self.errors.append({"type": kind, "command": message.get("command")})
        except (OSError, ValueError, queue.Full):
            self.errors.append({"type": "bridge_error"})

    def metrics(self):
        self.request_id += 1
        self.send({"type": "metrics", "id": self.request_id})
        try:
            message = self.replies.get(timeout=10)
        except queue.Empty as error:
            raise RuntimeError(self.variant + ": native host stopped responding") from error
        if message["id"] != self.request_id or self.errors:
            raise RuntimeError(f"{self.variant}: host protocol failed: {list(self.errors)}")
        if self.pid is None:
            self.pid = message["pid"]
        if not self.pid or message["pid"] != self.pid:
            raise RuntimeError(self.variant + ": WebContent process exited or changed")
        return message

    def record(self, directory, elapsed):
        metrics = self.metrics()
        identity, memory, raw = sample(self.pid)
        if self.identity is not None and identity != self.identity:
            raise RuntimeError(self.variant + ": WebContent PID was reused")
        self.identity = identity
        (directory / f"{self.variant}-{elapsed:08.1f}.txt").write_text(raw)
        if self.process.poll() is not None:
            raise RuntimeError(self.variant + ": native host exited")
        _, host_memory, host_raw = sample(self.process.pid)
        (directory / f"{self.variant}-host-{elapsed:08.1f}.txt").write_text(host_raw)
        return {"ui": metrics, "memory": memory, "frames": self.frames,
                "host_pid": self.process.pid, "host_memory": host_memory,
                "snapshot_requests": self.requests, "base64_mib": round(self.bytes / 1024**2, 3),
                "worker_acknowledgements": self.acknowledgements, "connection_failures": self.failures}

    def close(self):
        self.monitor.close()
        try:
            self.send({"type": "quit"})
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=3)
        self.log.close()


def check_state(metrics, action):
    if metrics["hidden"] != (action == "hide"):
        raise AssertionError("Visibility did not follow the host action")
    if metrics["playing"] != (action == "play"):
        raise AssertionError("Playback did not follow the host action")
    if action != "play" and (metrics["sourcePresent"] or metrics["liveURLs"]):
        raise AssertionError("A stopped or hidden player retained a frame URL")
    if metrics["liveURLs"] > 1:
        raise AssertionError("More than one Blob frame is pending")


def run(args, plugin, binary, pages, directory):
    players = []
    try:
        for variant in ("data", "blob"):
            mode = "player" if args.baseline_page else "bridge"
            players.append(Player(plugin, args.host, binary, pages[variant], variant, directory, mode))
        deadline = time.monotonic() + 15
        for player in players:
            if not player.ready.wait(timeout=max(0, deadline - time.monotonic())):
                raise RuntimeError(player.variant + ": page did not load")
        # Wait for the production UI's target announcement before clicking Play.
        while not all(player.metrics()["canPlay"] for player in players):
            if time.monotonic() > deadline:
                raise RuntimeError("The production player did not resolve its target")
            time.sleep(.1)
        tracemalloc.start()
        started = next_sample = time.monotonic()
        previous_phase = None
        phase_started = started
        checked_phase = None
        quiet_requests = {}
        phase_counts = {}
        with (directory / "samples.jsonl").open("x") as output:
            while True:
                now = time.monotonic()
                elapsed = now - started
                phase, action = phase_at(elapsed, args.cycle_seconds)
                cycle = int(elapsed // args.cycle_seconds)
                key = cycle, phase
                if key != previous_phase:
                    for player in players:
                        player.send({"type": "action", "action": action})
                    previous_phase, phase_started = key, now
                    quiet_requests = {}
                    phase_counts[phase] = phase_counts.get(phase, 0) + 1
                    print(json.dumps({"event": "phase", "seconds": round(elapsed, 1),
                                      "cycle": cycle, "phase": phase}), flush=True)
                if now - phase_started >= .5 and checked_phase != key:
                    for player in players:
                        check_state(player.metrics(), action)
                    checked_phase = key
                if action != "play" and now - phase_started > 4:
                    for player in players:
                        before = quiet_requests.setdefault(player.variant, player.requests)
                        if player.requests != before:
                            raise AssertionError(player.variant + ": camera requests continued while stopped")
                if action == "play" and now - phase_started > 30:
                    for player in players:
                        if now - player.last_frame_at > 30:
                            raise AssertionError(player.variant + ": no live frames for thirty seconds")
                rapid_in = args.cycle_seconds * 430 / 600 - elapsed % args.cycle_seconds
                near_rapid = phase.startswith("rapid-") or 0 < rapid_in < 5
                if (now >= next_sample and not near_rapid) or elapsed >= args.duration:
                    row = {"utc": datetime.now(timezone.utc).isoformat(),
                           "seconds": round(elapsed, 2), "cycle": cycle, "phase": phase,
                           "python_traced_mib": round(tracemalloc.get_traced_memory()[0] / 1024**2, 3),
                           "players": {}}
                    for player in players:
                        row["players"][player.variant] = player.record(directory / "vmmap", elapsed)
                    output.write(json.dumps(row) + "\n")
                    output.flush()
                    print(json.dumps({"event": "sample", "seconds": row["seconds"], "phase": phase,
                                      "players": {name: {"footprint_mib": value["memory"]["footprint_mib"],
                                                        "host_footprint_mib": value["host_memory"]["footprint_mib"],
                                                        "webkit_dirty_mib": value["memory"].get("WebKit Malloc", {}).get("dirty_mib"),
                                                        "frames": value["frames"]}
                                                  for name, value in row["players"].items()}}), flush=True)
                    next_sample = time.monotonic() + args.interval
                if elapsed >= args.duration:
                    break
                time.sleep(.1)
        completed_cycles = int(args.duration // args.cycle_seconds)
        required = ["playing-first", "stopped-first", "playing-second", "hidden",
                    "returned-stopped", "playing-third", "stopped-last"]
        required += [f"rapid-{step}" for step in range(20)]
        for phase in required:
            if phase_counts.get(phase, 0) < completed_cycles:
                raise AssertionError("The host skipped a scheduled phase: " + phase)
        for player in players:
            if player.frames < 100 or player.metrics()["loadedFrames"] < 100:
                raise AssertionError(player.variant + ": too few decoded frames for a useful run")
            with (directory / (player.variant + "-heap.txt")).open("w") as heap:
                subprocess.run(["/usr/bin/heap", "-s", "-H", "--noContent", str(player.pid)],
                               stdout=heap, stderr=subprocess.DEVNULL, check=True, timeout=30)
        result = {"passed": True, "seconds": round(time.monotonic() - started, 1), "phases": phase_counts}
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    finally:
        for player in reversed(players):
            player.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--page", type=Path, default=ROOT / "dist/ui/camera.html")
    parser.add_argument("--baseline-page", type=Path, help="Compare an earlier page with the current player, without bridge conversion")
    parser.add_argument("--duration", type=positive, default=1800, help="Seconds of measured playback and lifecycle cycles")
    parser.add_argument("--cycle-seconds", type=positive, default=600, help="Use 60 for a short smoke test")
    parser.add_argument("--interval", type=positive, default=15)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory for metrics; no camera images are saved")
    args = parser.parse_args()
    if sys.platform != "darwin" or args.cycle_seconds < 60:
        parser.error("macOS and a cycle of at least sixty seconds are required")
    plugin = load_plugin()
    args.host = plugin.printer_url(args.host)
    page = args.page.read_text()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "vmmap").mkdir()
    pages = {"data": args.output_dir / "data.html", "blob": args.output_dir / "blob.html"}
    baseline = args.baseline_page.read_text() if args.baseline_page else page
    pages["data"].write_text(baseline)
    if args.baseline_page:
        pages["blob"].write_text(page)
    else:
        if "img-src data:;" not in page:
            parser.error("Use --baseline-page to compare an earlier page with the current player's cleanup")
        pages["blob"].write_text(page.replace("img-src data:;", "img-src data: blob:;"))
    metadata = {"platform": platform.platform(), "python": platform.python_version(),
                "page_sha256": hashlib.sha256(page.encode()).hexdigest(),
                "baseline_sha256": hashlib.sha256(baseline.encode()).hexdigest(),
                "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "host_sha256": hashlib.sha256(Path(__file__).with_suffix(".swift").read_bytes()).hexdigest(),
                "duration": args.duration, "cycle_seconds": args.cycle_seconds,
                "visibility": "host-controlled document visibility in native WKWebView",
                "candidate": "production player" if args.baseline_page else "standalone message bridge"}
    (args.output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    with tempfile.TemporaryDirectory(prefix="u1-profile-") as temporary:
        binary = Path(temporary) / "webkit"
        subprocess.run(["/usr/bin/swiftc", str(Path(__file__).with_suffix(".swift")),
                        "-o", str(binary), "-module-cache-path", str(Path(temporary) / "module-cache")], check=True)
        try:
            run(args, plugin, binary, pages, args.output_dir)
        except Exception as error:
            (args.output_dir / "result.json").write_text(json.dumps({"passed": False, "error": str(error)}) + "\n")
            raise


if __name__ == "__main__":
    main()
