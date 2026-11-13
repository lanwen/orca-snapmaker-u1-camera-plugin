"""Plugin source. Run mise run build to produce the installable file."""

import base64
import csv
from collections import deque
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time

import orca

from http_client import HttpClient, LightCommand, ObjectQuery, printer_url


class UnsupportedPrinter(ValueError):
    """The selected Prepare preset is not a Snapmaker U1."""


def setting(value):
    return value.strip().strip('"') if isinstance(value, str) else ""


def is_u1_preset(preset):
    model = setting(preset.config_value("printer_model"))
    candidates = [model] if model else [preset.name, preset.config_value("inherits")]
    return any(re.search(r"\bsnapmaker[ _-]+u1\b", setting(value), re.IGNORECASE)
               for value in candidates)


def saved_connection(preset):
    """Read only the exact JSON preset identified by Orca, never its private app config."""
    path = Path(setting(preset.file))
    if path.suffix.lower() != ".json":
        return "", ""
    try:
        with path.open("rb") as source:
            raw = source.read(512_001)
        if len(raw) > 512_000:
            return "", ""
        data = json.loads(raw)
        if not isinstance(data, dict):
            return "", ""
        return setting(data.get("print_host")), setting(data.get("printhost_apikey"))
    except (OSError, ValueError):
        return "", ""


def nonnegative(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def filament_text(value):
    return value.strip() if isinstance(value, str) and value.strip() and value.strip().upper() != "NONE" else None


def filament_profile(metadata, config, tool):
    raw = metadata.get("filament_name")
    mapping = config.get("extruder_map_table")
    if (tool is None or metadata.get("slicer") not in ("OrcaSlicer", "SnapmakerOrca")
            or not isinstance(raw, str) or not isinstance(mapping, list)):
        return None
    used = metadata.get("filament_used_mm")
    parsed = set()
    # Snapmaker's metadata parser strips the outer quotes from serialized profile lists.
    candidates = (raw,) if raw.startswith('"') and raw.endswith('"') else (raw, '"' + raw + '"')
    for candidate in candidates:
        try:
            names = next(csv.reader([candidate], delimiter=";", escapechar="\\", doublequote=False, strict=True))
        except (csv.Error, StopIteration):
            continue
        if isinstance(used, list) and len(names) != len(used):
            continue
        parsed.add(tuple(filament_text(name) for name in names))
    if len(parsed) != 1:
        return None
    names = parsed.pop()
    if len(mapping) < len(names):
        return None
    profiles = {name for logical, name in enumerate(names)
                if type(mapping[logical]) is int and mapping[logical] == tool
                and (not isinstance(used, list) or nonnegative(used[logical]) != 0)}
    # Several sliced filaments can share a physical head; only show an unambiguous profile.
    return profiles.pop() if len(profiles) == 1 else None


def print_metrics(status, metadata=None):
    stats = status.get("print_stats") or {}
    state = stats.get("state", "Unknown")

    def fraction(value):
        value = nonnegative(value)
        return value if value is not None and value <= 1 else None

    def layer(value):
        value = nonnegative(value)
        return value if value is not None and value == int(value) else None

    display = fraction((status.get("display_status") or {}).get("progress"))
    byte_progress = fraction((status.get("virtual_sdcard") or {}).get("progress"))
    progress = display if display is not None else byte_progress
    duration = nonnegative(stats.get("print_duration"))
    info = stats.get("info") or {}
    current, total = layer(info.get("current_layer")), layer(info.get("total_layer"))
    phase = "unknown"  # Missing or contradictory layer markers cannot establish a phase.
    if current is not None and (total is None or current <= total):
        phase = "preparing" if current == 0 else "printing"
    metadata = metadata if isinstance(metadata, dict) else {}
    planned = nonnegative(metadata.get("estimated_time")) or None
    speed = nonnegative((status.get("gcode_move") or {}).get("speed_factor"))
    # Standard U1 fallback equals byte progress; retained Orca M73 is integer percent.
    m73 = (metadata.get("slicer") == "OrcaSlicer" and planned is not None
           and display is not None and byte_progress is not None
           and abs(display - byte_progress) > 0.000001
           and abs(display * 100 - round(display * 100)) <= 0.000001)
    remaining = None
    if state == "printing" and phase == "printing" and m73:
        if display == 1:
            phase = "finishing"  # M73 is complete but the printer still has work to finish.
        elif speed is not None and abs(speed - 1) <= 0.000001:
            remaining = planned * (1 - display)
    if state == "complete":
        progress, remaining = 1, 0
    name = (status.get("toolhead") or {}).get("extruder")
    match = re.fullmatch(r"extruder([1-3]?)", name) if isinstance(name, str) else None
    index = int(match[1] or 0) if match else None
    config = status.get("print_task_config") or {}

    def filament(field, tool):
        values = config.get(field)
        return values[tool] if isinstance(values, list) and tool < len(values) else None

    tools = []
    for tool in range(4):
        extruder = status.get("extruder" + (str(tool) if tool else ""))
        head = extruder if isinstance(extruder, dict) else {}
        physical = head.get("state")
        head_state = ("unavailable" if not isinstance(extruder, dict) else
                      "changing" if head.get("activating_move") is True else
                      "connected" if physical == "ACTIVATE" else
                      "parked" if physical == "PARKED" else
                      "unknown" if physical is not None else
                      "connected" if tool == index else "unknown")
        color = filament("filament_color_rgba", tool)
        color = "#" + color[:6] if isinstance(color, str) and re.fullmatch(r"[0-9a-fA-F]{8}", color) else None
        tools.append({"state": head_state,
                      "material": filament_text(filament("filament_type", tool)),
                      "material_vendor": filament_text(filament("filament_vendor", tool)),
                      "material_subtype": filament_text(filament("filament_sub_type", tool)),
                      "material_profile": filament_profile(metadata, config, tool),
                      "filament_present": filament("filament_exist", tool) if type(filament("filament_exist", tool)) is bool else None,
                      "color": color, "nozzle_diameter": nonnegative(head.get("nozzle_diameter")) or None,
                      "heater": {key: nonnegative(head.get(key)) for key in ("temperature", "target")}})
    connected = [tool for tool, head in enumerate(tools) if head["state"] == "connected"]

    def fan(source):
        source = source if isinstance(source, dict) else {}
        speed = nonnegative(source.get("speed"))
        return {"speed": speed if speed is not None and speed <= 1 else None,
                "rpm": nonnegative(source.get("rpm"))}

    part_fan = ("fan" if connected[0] == 0 else "fan_generic e%d_fan" % connected[0]) if len(connected) == 1 else None
    hotend_fan = "heater_fan e%d_nozzle_fan" % connected[0] if len(connected) == 1 else None
    purifier = status.get("purifier")
    purifier = purifier if isinstance(purifier, dict) and purifier.get("power_detected") is True else {}
    circulation = fan(purifier.get("inner_fan"))
    circulation["rpm"] = nonnegative(purifier.get("inner_fan_rpm"))
    active = tools[index] if index is not None else {}
    return {"state": state, "progress": progress,
            "current_layer": current,
            "total_layer": total,
            "elapsed": nonnegative(stats.get("total_duration")), "printing_duration": duration,
            "remaining": remaining, "planned_total": planned, "phase": phase,
            "tool": index, "tools": tools,
            "aux_fan": fan(status.get("fan_generic cavity_fan")), "part_fan": fan(status.get(part_fan)),
            "psu_fan": fan(status.get("heater_fan power_fan")),
            "hotend_fan": fan(status.get(hotend_fan)),
            "exhaust_fan": fan(purifier.get("exhaust_fan")), "circulation_fan": circulation,
            "layer_height": nonnegative(metadata.get("layer_height")) or None,
            "material": active.get("material"),
            "material_vendor": active.get("material_vendor"),
            "material_subtype": active.get("material_subtype"),
            "material_profile": active.get("material_profile"),
            "color": active.get("color"), "bed": status.get("heater_bed") or {},
            "hotend": status.get(name) or {} if match else {}}


class Printer:
    def __init__(self, address, api_key=""):
        self.http = HttpClient(address, api_key)
        self.last_hash = None
        self.frame_times = deque(maxlen=30)
        self.previous_run = None
        self.metadata = None
        self.metadata_retry_at = 0

    def start_monitor(self):
        self.http.start_monitor()

    def snapshot(self):
        snapshot = self.http.camera_snapshot()
        data = snapshot.data
        digest = hashlib.sha256(data).digest()
        now = time.monotonic()
        if digest != self.last_hash:
            self.last_hash = digest
            self.frame_times.append(now)
        fps = ((len(self.frame_times) - 1) / (now - self.frame_times[0])
               if len(self.frame_times) > 1 else 0)
        return {"image": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
                "fps": round(fps, 1),
                "frozen": snapshot.modified_at is not None and time.time() - snapshot.modified_at > 5}

    def status(self):
        status = self.http.query_objects(ObjectQuery("webhooks"), ObjectQuery("print_stats"),
                                         ObjectQuery("led cavity_led"), ObjectQuery("display_status"),
                                         ObjectQuery("virtual_sdcard", ("progress", "file_position", "file_size")),
                                         ObjectQuery("gcode_move", ("speed_factor",)),
                                         ObjectQuery("toolhead", ("extruder",)),
                                         ObjectQuery("print_task_config", ("filament_type", "filament_color_rgba",
                                                                          "filament_vendor", "filament_sub_type",
                                                                          "extruder_map_table", "filament_exist")),
                                         ObjectQuery("heater_bed", ("temperature", "target")),
                                         ObjectQuery("purifier", ("power_detected", "exhaust_fan", "inner_fan", "inner_fan_rpm")),
                                         *(ObjectQuery(name, ("speed", "rpm")) for name in
                                           ("fan_generic cavity_fan", "fan", "fan_generic e1_fan",
                                            "fan_generic e2_fan", "fan_generic e3_fan", "heater_fan power_fan",
                                            "heater_fan e0_nozzle_fan", "heater_fan e1_nozzle_fan",
                                            "heater_fan e2_nozzle_fan", "heater_fan e3_nozzle_fan")),
                                         *(ObjectQuery(name, ("temperature", "target", "nozzle_diameter",
                                                              "state", "activating_move")) for name in
                                           ("extruder", "extruder1", "extruder2", "extruder3")))
        stats = status.get("print_stats") or {}
        sd = status.get("virtual_sdcard") or {}
        state = stats.get("state")
        active = state in ("printing", "paused")
        filename = stats.get("filename")
        run = (state, filename, nonnegative(stats.get("total_duration")),
               nonnegative(sd.get("file_position")), nonnegative(sd.get("file_size")))
        previous = self.previous_run
        new_run = (previous is None or filename != previous[1] or run[4] != previous[4]
                   or active and previous[0] not in ("printing", "paused")
                   or any(run[i] is not None and previous[i] is not None and run[i] < previous[i]
                          for i in (2, 3)))
        self.previous_run = run
        if new_run or not active:
            self.metadata, self.metadata_retry_at = None, 0
        now = time.monotonic()
        if active and isinstance(filename, str) and filename and now >= self.metadata_retry_at:
            self.metadata_retry_at = now + 30
            self.metadata = None
            try:
                metadata = self.http.file_metadata(filename)
                if isinstance(metadata, dict):
                    size = nonnegative(metadata.get("size"))
                    if size is None or run[4] is None or size == run[4]:
                        self.metadata = metadata
            except Exception:
                pass  # Optional metadata must not hide live printer status.
        led = status.get("led cavity_led", {}).get("color_data")
        return {"printer": status.get("print_stats", {}).get("state", "Unknown"),
                "light": bool(led[0][3]) if led else None, "stats": print_metrics(status, self.metadata)}

    def set_light(self, enabled):
        self.http.set_light(LightCommand(enabled))

    def close(self):
        self.http.close()


class Monitor:
    """One worker; short page leases prevent abandoned views from keeping cameras awake."""

    def __init__(self, publish, client_factory=Printer):
        self.publish = publish
        self.client_factory = client_factory
        self.condition = threading.Condition()
        self.target = None
        self.generation = 0
        self.expires = 0
        self.light = None
        self.playing = False
        self.frame_ready = True
        self.closed = False
        self.client = None
        self.thread = threading.Thread(target=self.run, name="U1 camera", daemon=True)
        self.thread.start()

    def update(self, target, enabled=None, playing=None):
        with self.condition:
            target_changed = target != self.target
            playback_changed = type(playing) is bool and playing != self.playing
            if target_changed or playback_changed:
                if self.client is not None:
                    self.close_client(self.client)
                self.generation += 1
                self.frame_ready = True
            if target_changed:
                self.light = None
            self.target = target
            if type(playing) is bool:
                self.playing = playing
            if target is None:
                self.playing = False
            self.expires = time.monotonic() + 3.5 if target else 0
            if type(enabled) is bool and target:
                self.light = enabled
            # Announce the generation before the worker can publish a frame for it.
            if target:
                self.publish({"type": "target", "target": target[0], "generation": self.generation})
            self.condition.notify()
            return self.generation

    def frame_loaded(self, generation):
        with self.condition:
            if generation == self.generation and self.playing and not self.closed:
                self.frame_ready = True
                self.expires = time.monotonic() + 3.5
                self.condition.notify()

    def current(self, generation):
        with self.condition:
            return (not self.closed and self.target is not None
                    and generation == self.generation and time.monotonic() < self.expires)

    def emit(self, generation, data):
        if self.current(generation):
            self.publish(dict(data, generation=generation))

    def run(self):
        client, generation = None, -1
        keepalive_at = status_at = retry_at = 0
        try:
            while True:
                with self.condition:
                    if self.closed:
                        return
                    target = self.target if time.monotonic() < self.expires else None
                    selected_generation = self.generation
                    playing = self.playing
                if client is not None and (not target or generation != selected_generation):
                    self.close_client(client)
                    client = None
                    retry_at = 0
                if not target:
                    with self.condition:
                        self.condition.wait(timeout=0.2)
                    continue
                if client is None and time.monotonic() < retry_at:
                    with self.condition:
                        self.condition.wait(timeout=0.2)
                    continue
                try:
                    if client is None:
                        generation = selected_generation
                        client = self.client_factory(*target)
                        with self.condition:
                            self.client = client
                            if self.closed or generation != self.generation:
                                self.close_client(client)
                        keepalive_at = status_at = 0
                    if not self.current(generation):
                        continue
                    with self.condition:
                        if generation != self.generation:
                            continue
                        light, self.light = self.light, None
                    if light is not None:
                        try:
                            client.set_light(light)
                        except Exception:
                            self.emit(generation, {"light_error": "Could not change light — try again"})
                        status_at = 0
                    if not self.current(generation):
                        continue
                    if time.monotonic() >= status_at:
                        try:
                            self.emit(generation, client.status())
                        except Exception:
                            self.emit(generation, {"printer": "Status unavailable", "light": None, "stats": None})
                        status_at = time.monotonic() + 3
                    if not self.current(generation):
                        continue
                    if not playing or time.monotonic() < retry_at:
                        with self.condition:
                            self.condition.wait(timeout=0.2)
                        continue
                    if time.monotonic() >= keepalive_at:
                        client.start_monitor()
                        keepalive_at = time.monotonic() + 60
                    if not self.current(generation):
                        continue
                    with self.condition:
                        fetch_frame = (generation == self.generation
                                       and self.playing and self.frame_ready)
                        if fetch_frame:
                            self.frame_ready = False
                    if fetch_frame:
                        self.emit(generation, client.snapshot())
                except Exception as error:
                    # Do not expose exception URLs/headers, which can contain credentials.
                    message = str(error) if isinstance(error, RuntimeError) else "Cannot reach printer — check its address, API key, and connection"
                    self.emit(generation, {"camera": message, "failed": True})
                    keepalive_at = 0
                    with self.condition:
                        self.frame_ready = True
                    retry_at = time.monotonic() + 3 if self.current(generation) else 0
                with self.condition:
                    # Image-load acknowledgements wake this immediately; no frame-rate cap.
                    if time.monotonic() < retry_at or not (self.playing and self.frame_ready):
                        self.condition.wait(timeout=0.2)
        finally:
            if client is not None:
                self.close_client(client)

    def close_client(self, client):
        with self.condition:
            if self.client is client:
                self.client = None
                client.close()

    def close(self):
        with self.condition:
            self.closed = True
            if self.client is not None:
                self.close_client(self.client)
            self.condition.notify()
        self.thread.join(timeout=3)


PAGE = "__CAMERA_HTML__"
ICON = "__CAMERA_SVG__"


class U1Page(orca.pages.PagesPluginCapabilityBase):
    def __init__(self):
        super().__init__()
        self.monitor = None
        self.preset_connection_prompt_open = False
        self.preset_connection_save_failed = False

    def get_name(self):
        return "Snapmaker U1"

    def get_icon(self):
        # Orca's native tab needs a file path, not an HTML favicon or data URL.
        path = Path(__file__).with_suffix(".svg")
        path.write_text(ICON, encoding="utf-8")
        return str(path)

    def get_default_config(self):
        return {"host": "", "allow_preset_connection": None}

    def get_ui(self):
        if self.monitor:
            self.monitor.update(None)
        return PAGE

    def allow_preset_connection(self, config):
        allowed = config.get("allow_preset_connection")
        if type(allowed) is bool:
            return allowed
        if self.preset_connection_prompt_open or self.preset_connection_save_failed:
            return False
        # Fail closed if the modal dialog lets another page message run.
        self.preset_connection_prompt_open = True
        try:
            choice = orca.host.ui.message(
                "Allow this plugin to read the selected Snapmaker U1 preset's printer address and API key?\n\n"
                "The connection fields are print_host and printhost_apikey. "
                "If Orca's API has no address, it reads only the selected preset's JSON file.\n\n"
                "Your choice applies to all selected U1 presets and is saved in plugin Config "
                "as allow_preset_connection. Choose No to set host manually instead.",
                title="Snapmaker U1: printer connection access", buttons="yes_no", icon="question")
            config = json.loads(self.get_config())
            if config.get("allow_preset_connection") is False:
                return False
            allowed = choice == "yes"
            if not self.save_config(json.dumps(dict(config, allow_preset_connection=allowed))):
                self.preset_connection_save_failed = True
                raise ValueError('Could not save your preset access choice. Set "host" manually or reload the plugin to retry.')
            effective = json.loads(self.get_config()).get("allow_preset_connection")
            if effective is not allowed:
                self.preset_connection_save_failed = True
                raise ValueError('Could not confirm your preset access choice. Check Config or set "host" manually.')
            return allowed
        finally:
            self.preset_connection_prompt_open = False

    def selected_target(self):
        config = json.loads(self.get_config())
        # A manual host must never receive another selected printer's credentials.
        if config.get("host"):
            return printer_url(config["host"]), ""
        bundle = orca.host.preset_bundle()
        preset = bundle.current_printer_preset()
        selection = setting(preset.file), setting(preset.name)
        if not is_u1_preset(preset):
            raise UnsupportedPrinter('Cannot determine a Snapmaker U1 address. Select a U1 preset or set "host" in plugin Config.')
        if not self.allow_preset_connection(config):
            raise ValueError('Preset connection access is disabled. Set "host" in plugin Config, '
                             'or reset "allow_preset_connection" to null to ask again.')
        # The modal consent dialog can dispatch selection events; refresh host handles.
        bundle = orca.host.preset_bundle()
        preset = bundle.current_printer_preset()
        if selection != (setting(preset.file), setting(preset.name)) or not is_u1_preset(preset):
            raise ValueError("Printer selection changed. Try again with the selected preset.")
        address = setting(preset.config_value("print_host")) or setting(bundle.full_config_value("print_host"))
        key = setting(preset.config_value("printhost_apikey")) or setting(bundle.full_config_value("printhost_apikey"))
        if not address:
            address, key = saved_connection(preset)
        return printer_url(address), key

    def on_lifecycle_event(self, event, ctx):
        if (event == orca.LifecycleEvent.DeviceSelected
                or event == orca.LifecycleEvent.PresetSelected and ctx.msg == "printer"):
            if self.monitor:
                self.monitor.update(None)
            # Resolve fresh host handles later, in the page's UI-thread message callback.
            self.post_message({"type": "selection_changed"})

    def on_message(self, data):
        if not isinstance(data, dict):
            return
        if data.get("type") == "frame":
            if self.monitor:
                self.monitor.frame_loaded(data.get("generation"))
            return
        if data.get("type") == "activity" and data.get("active") is not True:
            if self.monitor:
                self.monitor.update(None)
            return
        if data.get("type") not in ("activity", "light"):
            return
        try:
            target = self.selected_target()  # Copy host values on the page's UI callback.
            if self.monitor is None:
                self.monitor = Monitor(self.post_message)
            enabled = data.get("enabled") if data.get("type") == "light" else None
            playing = data.get("playing") if data.get("type") == "activity" else None
            self.monitor.update(target, enabled, playing)
        except Exception as error:
            if self.monitor:
                self.monitor.update(None)
            message = str(error) if isinstance(error, ValueError) else "Cannot read the selected printer. Set host in plugin Config."
            self.post_message({"error": message, "supported": not isinstance(error, UnsupportedPrinter)})

    def on_unload(self):
        if self.monitor:
            self.monitor.close()
            self.monitor = None


@orca.plugin
class U1Plugin(orca.base):
    def register_capabilities(self):
        orca.request_permissions(threading=True)
        orca.register_capability(U1Page)
