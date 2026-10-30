"""Plugin source. Run mise run build to produce the installable file."""

import base64
from collections import deque
import hashlib
import json
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


class Printer:
    def __init__(self, address, api_key=""):
        self.http = HttpClient(address, api_key)
        self.last_hash = None
        self.changed_at = time.monotonic()
        self.frame_times = deque(maxlen=30)

    def start_monitor(self):
        self.http.start_monitor()

    def snapshot(self):
        snapshot = self.http.camera_snapshot()
        data = snapshot.data
        digest = hashlib.sha256(data).digest()
        now = time.monotonic()
        if digest != self.last_hash:
            self.changed_at, self.last_hash = now, digest
            self.frame_times.append(now)
        fps = ((len(self.frame_times) - 1) / (now - self.frame_times[0])
               if len(self.frame_times) > 1 else 0)
        if snapshot.modified_at is not None:
            age = time.time() - snapshot.modified_at
            state = "Stale image — camera has not updated" if age > 15 else "Live · %.1f FPS" % fps
        else:
            state = ("Image unchanged — freshness unconfirmed" if now - self.changed_at > 20
                     else "Receiving images · timestamp unavailable")
        return {"image": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
                "camera": state}

    def status(self):
        status = self.http.query_objects(ObjectQuery("webhooks"), ObjectQuery("print_stats"),
                                         ObjectQuery("led cavity_led"))
        led = status.get("led cavity_led", {}).get("color_data")
        return {"printer": status.get("print_stats", {}).get("state", "Unknown"),
                "light": bool(led[0][3]) if led else None}

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
        self.thread = threading.Thread(target=self.run, name="U1 camera", daemon=True)
        self.thread.start()

    def update(self, target, enabled=None, playing=None):
        with self.condition:
            target_changed = target != self.target
            playback_changed = type(playing) is bool and playing != self.playing
            if target_changed or playback_changed:
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
                    client.close()
                    client = None
                if not target:
                    with self.condition:
                        self.condition.wait(timeout=0.2)
                    continue
                if time.monotonic() < retry_at:
                    with self.condition:
                        self.condition.wait(timeout=0.2)
                    continue
                try:
                    if client is None:
                        generation = selected_generation
                        client = self.client_factory(*target)
                        keepalive_at = status_at = 0
                    if not self.current(generation):
                        continue
                    if playing and time.monotonic() >= keepalive_at:
                        client.start_monitor()
                        keepalive_at = time.monotonic() + 10
                    if not self.current(generation):
                        continue
                    with self.condition:
                        if generation != self.generation:
                            continue
                        light, self.light = self.light, None
                    if light is not None:
                        client.set_light(light)
                        status_at = 0
                    if not self.current(generation):
                        continue
                    if time.monotonic() >= status_at:
                        try:
                            self.emit(generation, client.status())
                        except Exception:
                            self.emit(generation, {"printer": "Status unavailable", "light": None})
                        status_at = time.monotonic() + 3
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
                    message = str(error) if isinstance(error, RuntimeError) else "Cannot reach the printer. Check its address, API key, and connection."
                    self.emit(generation, {"camera": message, "failed": True})
                    if client is not None:
                        client.close()
                    client = None
                    with self.condition:
                        self.frame_ready = True
                    retry_at = time.monotonic() + 3
                with self.condition:
                    # Image-load acknowledgements wake this immediately; no frame-rate cap.
                    if not (self.playing and self.frame_ready):
                        self.condition.wait(timeout=0.2)
        finally:
            if client is not None:
                client.close()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify()
        self.thread.join(timeout=3)


PAGE = "__CAMERA_HTML__"


class U1Page(orca.pages.PagesPluginCapabilityBase):
    def __init__(self):
        super().__init__()
        self.monitor = None
        self.preset_connection_prompt_open = False
        self.preset_connection_save_failed = False

    def get_name(self):
        return "Snapmaker U1"

    def get_default_config(self):
        return {"host": "", "allow_preset_connection": {}}

    def get_ui(self):
        if self.monitor:
            self.monitor.update(None)
        return PAGE

    def allow_preset_connection(self, config, preset):
        permissions = config.get("allow_preset_connection")
        if permissions is False:
            return False
        # Legacy global approvals must be confirmed for the selected preset.
        permissions = permissions if isinstance(permissions, dict) else {}
        file, name = setting(preset.file), setting(preset.name)
        if not file and not name:
            raise ValueError('Cannot identify the selected preset. Set "host" in plugin Config.')
        identity = "file:" + file if file else "name:" + name
        allowed = permissions.get(identity)
        if type(allowed) is bool:
            return allowed
        if self.preset_connection_prompt_open or self.preset_connection_save_failed:
            return False
        # Fail closed if the modal dialog lets another page message run.
        self.preset_connection_prompt_open = True
        try:
            file_description = f"Preset file: {file}\n\n" if file else ""
            choice = orca.host.ui.message(
                f'Allow this plugin to read the printer address and API key for preset "{name}"?\n\n{file_description}'
                "The connection fields are print_host and printhost_apikey. "
                "If Orca's API has no address, it reads only this preset's JSON file.\n\n"
                "Your choice applies only to this preset and is saved in plugin Config "
                "as allow_preset_connection. Choose No to set host manually instead.",
                title="Snapmaker U1: printer connection access", buttons="yes_no", icon="question")
            config = json.loads(self.get_config())
            if config.get("allow_preset_connection") is False:
                return False
            permissions = config.get("allow_preset_connection")
            permissions = permissions if isinstance(permissions, dict) else {}
            allowed = choice == "yes"
            permissions = {**permissions, identity: allowed}
            if not self.save_config(json.dumps(dict(config, allow_preset_connection=permissions))):
                self.preset_connection_save_failed = True
                raise ValueError('Cannot save preset permission. Set "host" manually or reload the plugin to retry.')
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
        if not self.allow_preset_connection(config, preset):
            raise ValueError('Preset connection access is disabled. Set "host" in plugin Config, '
                             'or reset "allow_preset_connection" to {} to ask again.')
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
