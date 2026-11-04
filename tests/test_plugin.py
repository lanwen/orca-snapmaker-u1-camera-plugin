"""Client protocol and worker tests; no Orca instance or physical printer needed."""
import importlib.util
import json
import re
from pathlib import Path
import sys
import threading
import time
import types
import tempfile
import unittest
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


orca = types.ModuleType("orca")
orca.pages = types.SimpleNamespace(PagesPluginCapabilityBase=object)
orca.base = object
orca.plugin = lambda cls: cls
orca.LifecycleEvent = types.SimpleNamespace(DeviceSelected="device", PresetSelected="preset")
sys.modules["orca"] = orca
spec = importlib.util.spec_from_file_location("plugin", Path(__file__).parents[1] / "dist" / "orca-snapmaker-u1-camera-plugin_any.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class PageIconTests(unittest.TestCase):
    def test_native_tab_icon_is_available_before_loading_page_html(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(plugin, "__file__", str(Path(directory) / "camera.py")):
                page = plugin.U1Page()
                icon = Path(page.get_icon())
                self.assertTrue(icon.is_absolute())
                self.assertEqual(icon.suffix, ".svg")
                self.assertEqual(icon.parent, Path(directory))
                expected = (Path(__file__).parents[1] / "docs/branding/u1-logo.svg").read_text().replace("#249b7c", "#FEFEFE")
                self.assertEqual(icon.read_text(), expected)
                self.assertEqual(page.get_icon(), str(icon))


class Endpoint(BaseHTTPRequestHandler):
    modified = "Thu, 01 Jan 1970 00:00:00 GMT"
    requests = []
    payloads = []

    def do_GET(self):
        self.requests.append((self.path, self.headers.get("X-Api-Key")))
        self.send_response(302 if self.path == "/redirect" else 401 if self.path == "/denied" else 200)
        if self.path == "/redirect":
            self.send_header("Location", "http://example.com")
        self.send_header("Last-Modified", self.modified)
        self.end_headers()
        if self.path.startswith("/server/files"):
            self.wfile.write(b"\xff\xd8test-jpeg")
        elif self.path == "/large":
            self.wfile.write(b"123456")
        elif self.path.startswith("/printer/objects/query?"):
            self.wfile.write(json.dumps({"result": {"status": {
                "print_stats": {"state": "standby"},
                "led cavity_led": {"color_data": [[0, 0, 0, 1]]},
            }}}).encode())
        else:
            self.wfile.write(b'{"result":"token"}')

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.payloads.append(payload)
        self.requests.append((self.path, self.headers.get("X-Api-Key"), self.headers.get("Content-Type")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"result": payload}).encode())

    def log_message(self, *args):
        pass


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.address = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_address_validation(self):
        self.assertEqual(plugin.printer_url("printer.local:7125"), "http://printer.local:7125")
        for value in ["", "file:///tmp/a", "http://user:secret@printer", "http://printer/path", "http://printer?token=x", "http://printer:99999"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                plugin.printer_url(value)

    def test_snapshot_marks_old_timestamp_stale(self):
        printer = plugin.Printer(self.address, "secret")
        frame = printer.snapshot()
        self.assertIn("Stale", frame["camera"])
        self.assertTrue(frame["image"].startswith("data:image/jpeg;base64,"))
        first_request = Endpoint.requests[-1][0]
        printer.snapshot()
        self.assertNotEqual(Endpoint.requests[-1][0], first_request,
                            "Repeated snapshots must bypass the HTTP cache")
        self.assertEqual(Endpoint.requests[-1][1], "secret")

    def test_fps_counts_new_images_instead_of_downloads(self):
        printer = plugin.Printer(self.address)
        printer.http.camera_snapshot = Mock(side_effect=[plugin.CameraSnapshot(b'\xff\xd8first', 1_000_000_000),
                                        plugin.CameraSnapshot(b'\xff\xd8first', 1_000_000_000),
                                        plugin.CameraSnapshot(b'\xff\xd8second', 1_000_000_000)])
        with patch.object(plugin.time, "monotonic", side_effect=[10, 10.1, 10.2]), \
                patch.object(plugin.time, "time", return_value=1_000_000_000):
            displayed_rates = [float(re.search(r"([\d.]+)\s*FPS", printer.snapshot()["camera"])[1])
                               for _ in range(3)]
            self.assertEqual(displayed_rates, [0, 0, 5],
                             "Downloading an identical image must not increase the displayed frame rate")

    def test_http_does_not_follow_redirect_or_accept_oversized_data(self):
        client = plugin.HttpClient(self.address)
        with self.assertRaisesRegex(RuntimeError, "302"):
            client.request("/redirect")
        with self.assertRaisesRegex(RuntimeError, "too large"):
            client.request("/large", limit=5)

    def test_http_json_posts_payload_with_api_key(self):
        client = plugin.HttpClient(self.address, "secret")
        self.assertEqual(client.json("/echo", {"enabled": True}), {"result": {"enabled": True}})
        self.assertEqual(Endpoint.requests[-1], ("/echo", "secret", "application/json"))

    def test_http_denied_access_has_a_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "denied access"):
            plugin.HttpClient(self.address).request("/denied")

    def test_keepalive_and_oneshot_authentication(self):
        ws = Mock()
        ws.recv.side_effect = [json.dumps({"method": "notification"}), json.dumps({"id": 1, "result": {}})]
        with patch.object(plugin.websocket, "create_connection", return_value=ws) as connect:
            plugin.Printer(self.address, "api-key").start_monitor()
        self.assertEqual(json.loads(ws.send.call_args.args[0]), {
            "jsonrpc": "2.0", "id": 1, "method": "camera.start_monitor",
            "params": {"domain": "lan", "interval": 0}})
        self.assertTrue(connect.call_args.args[0].endswith("/websocket?token=token"))
        self.assertEqual(connect.call_args.kwargs["http_no_proxy"], ["*"])

    def test_camera_rejection(self):
        printer = plugin.Printer(self.address)
        ws = Mock()
        ws.recv.return_value = json.dumps({"id": 1, "error": {"message": "denied"}})
        with patch.object(plugin.websocket, "create_connection", return_value=ws), \
                self.assertRaisesRegex(RuntimeError, "rejected"):
            printer.start_monitor()

    def test_monitor_reuses_connection_matches_ids_and_closes_once(self):
        printer = plugin.Printer(self.address)
        ws = Mock()
        ws.recv.side_effect = [json.dumps({"id": 1, "result": {}}),
                               json.dumps({"id": 1, "result": {}}),
                               json.dumps({"id": 2, "result": {}})]
        with patch.object(plugin.websocket, "create_connection", return_value=ws) as connect:
            printer.start_monitor()
            printer.start_monitor()
        connect.assert_called_once()
        self.assertEqual([json.loads(call.args[0])["id"] for call in ws.send.call_args_list], [1, 2])
        self.assertEqual(ws.recv.call_count, 3)
        printer.close()
        printer.close()
        ws.close.assert_called_once_with(timeout=0)

    def test_monitor_limits_responses_and_wait_time(self):
        for reply, error in [('x' * 512_001, 'too large'),
                             (json.dumps({"method": "notification"}), 'did not acknowledge')]:
            with self.subTest(error=error):
                client = plugin.HttpClient(self.address)
                ws = Mock()
                ws.recv.return_value = reply
                with patch.object(plugin.websocket, "create_connection", return_value=ws), \
                        self.assertRaisesRegex(RuntimeError, error):
                    client.start_monitor()
                self.assertLessEqual(ws.recv.call_count, 100)
                client.close()
        client = plugin.HttpClient(self.address)
        with patch.object(plugin.websocket, "create_connection", return_value=Mock()), \
                patch.object(plugin.time, "monotonic", side_effect=[0, 3]), \
                self.assertRaisesRegex(RuntimeError, 'did not acknowledge'):
            client.start_monitor()
        client.close()

    def test_snapshot_validates_jpeg_and_decodes_optional_timestamp(self):
        client = plugin.HttpClient(self.address)
        with patch.object(client, "request", return_value=(b'not a jpeg', None)), \
                self.assertRaisesRegex(RuntimeError, 'JPEG'):
            client.camera_snapshot()
        with patch.object(client, "request", return_value=(b'\xff\xd8image', 'invalid')), \
                self.assertRaisesRegex(RuntimeError, 'timestamp'):
            client.camera_snapshot()
        with patch.object(client, "request", return_value=(b'\xff\xd8image', None)):
            self.assertEqual(client.camera_snapshot(), plugin.CameraSnapshot(b'\xff\xd8image', None))

    def test_light_only_sends_fixed_led_command(self):
        printer = plugin.Printer(self.address)
        printer.set_light(True)
        self.assertEqual(Endpoint.requests[-1][0], "/printer/gcode/script")
        self.assertEqual(Endpoint.payloads[-1], {"script": "SET_LED LED=cavity_led WHITE=1 SYNC=0"})
        printer.set_light(False)
        self.assertEqual(Endpoint.payloads[-1], {"script": "SET_LED LED=cavity_led WHITE=0 SYNC=0"})
        request_count = len(Endpoint.requests)
        with self.assertRaises(ValueError):
            printer.set_light("M112")
        self.assertEqual(len(Endpoint.requests), request_count)

    def test_object_queries_encode_names_and_fields_and_unwrap_status(self):
        client = plugin.HttpClient(self.address, "secret")
        status = client.query_objects(plugin.ObjectQuery("print_stats", ("state", "filename")),
                                      plugin.ObjectQuery("led cavity_led&other"))
        self.assertEqual(status["print_stats"]["state"], "standby")
        self.assertEqual(Endpoint.requests[-1], (
            "/printer/objects/query?print_stats=state,filename&led%20cavity_led%26other", "secret"))
        result = plugin.Printer(self.address).status()
        self.assertEqual(result["printer"], "standby")
        self.assertTrue(result["light"])
        self.assertIsNone(result["stats"]["progress"])

    def estimate_status(self):
        return {"print_stats": {"state": "printing", "filename": "part.gcode",
                                "print_duration": 3000, "total_duration": 3100,
                                "info": {"current_layer": 30, "total_layer": 100}},
                "display_status": {"progress": .36},
                "virtual_sdcard": {"progress": .30966, "file_position": 300, "file_size": 1000},
                "gcode_move": {"speed_factor": 1}}

    def test_orca_time_plan_uses_confirmed_progress_and_model_phase(self):
        metadata = {"slicer": "OrcaSlicer", "estimated_time": 2081}
        result = plugin.print_metrics(self.estimate_status(), metadata)
        self.assertAlmostEqual(result["remaining"], 1331.84)
        self.assertEqual(result["planned_total"], 2081)
        self.assertEqual(result["phase"], "printing")
        for layer, phase in [(0, "preparing"), (None, "unknown"), (.5, "unknown"),
                             (True, "unknown"), (-1, "unknown"), (101, "unknown")]:
            with self.subTest(layer=layer):
                status = self.estimate_status()
                status["print_stats"]["info"]["current_layer"] = layer
                result = plugin.print_metrics(status, metadata)
                self.assertEqual(result["phase"], phase)
                self.assertIsNone(result["remaining"])
                self.assertEqual(result["planned_total"], 2081)

    def test_estimation_rejects_ambiguous_or_invalid_sources(self):
        metadata = {"slicer": "OrcaSlicer", "estimated_time": 2081}
        cases = [("display_status", "progress", value)
                 for value in (None, True, -1, 1.1, float("nan"), float("inf"), .365)]
        cases += [("virtual_sdcard", "progress", value) for value in (None, .36, True, -1, 2)]
        cases += [("gcode_move", "speed_factor", value) for value in (None, True, .5, 2)]
        for section, field, value in cases:
            with self.subTest(section=section, value=value):
                status = self.estimate_status()
                status[section][field] = value
                self.assertIsNone(plugin.print_metrics(status, metadata)["remaining"])
        for invalid in (None, [], {"estimated_time": 2081},
                        {"slicer": "SnapmakerOrca", "estimated_time": 2081}):
            self.assertIsNone(plugin.print_metrics(self.estimate_status(), invalid)["remaining"])
        for value in (None, True, 0, -1, "2081", float("nan"), float("inf")):
            result = plugin.print_metrics(self.estimate_status(), dict(metadata, estimated_time=value))
            self.assertIsNone(result["remaining"])
            self.assertIsNone(result["planned_total"])
        for display, byte_progress in [(.360000001, .30), (.36, .3600005)]:
            status = self.estimate_status()
            status["display_status"]["progress"] = display
            status["virtual_sdcard"]["progress"] = byte_progress
            remaining = plugin.print_metrics(status, metadata)["remaining"]
            self.assertEqual(remaining is not None, byte_progress == .30)

    def test_pause_resume_finishing_and_terminal_estimates(self):
        status = self.estimate_status()
        metadata = {"slicer": "OrcaSlicer", "estimated_time": 2081}
        for state in ("paused", "cancelled", "error", "standby"):
            status["print_stats"]["state"] = state
            self.assertIsNone(plugin.print_metrics(status, metadata)["remaining"])
        status["print_stats"]["state"] = "printing"
        self.assertAlmostEqual(plugin.print_metrics(status, metadata)["remaining"], 1331.84)
        status["display_status"]["progress"] = .30966
        self.assertIsNone(plugin.print_metrics(status, metadata)["remaining"])
        status["display_status"]["progress"] = 1
        result = plugin.print_metrics(status, metadata)
        self.assertEqual(result["phase"], "finishing")
        self.assertIsNone(result["remaining"])
        status["print_stats"]["state"] = "complete"
        result = plugin.print_metrics(status, metadata)
        self.assertEqual((result["remaining"], result["progress"]), (0, 1))

    def test_metadata_cache_tracks_run_resets_and_refreshes_with_bounded_retry(self):
        printer = plugin.Printer("http://printer.local")
        status = self.estimate_status()
        printer.http.query_objects = Mock(return_value=status)
        printer.http.file_metadata = Mock(return_value={"slicer": "OrcaSlicer", "estimated_time": 2081,
                                                        "modified": 1})
        with patch.object(plugin.time, "monotonic", return_value=100) as clock:
            self.assertAlmostEqual(printer.status()["stats"]["remaining"], 1331.84)
            queries = printer.http.query_objects.call_args.args
            self.assertIn(plugin.ObjectQuery("gcode_move", ("speed_factor",)), queries)
            self.assertIn(plugin.ObjectQuery("virtual_sdcard", ("progress", "file_position", "file_size")), queries)
            for state in ("paused", "printing"):
                status["print_stats"]["state"] = state
                printer.status()
            self.assertEqual(printer.http.file_metadata.call_count, 1)
            clock.return_value = 130
            printer.http.file_metadata.return_value = {"slicer": "OrcaSlicer", "estimated_time": 3000,
                                                        "modified": 2}
            self.assertEqual(printer.status()["stats"]["planned_total"], 3000)
            printer.http.file_metadata.side_effect = RuntimeError("Unavailable")
            clock.return_value = 160
            self.assertIsNone(printer.status()["stats"]["planned_total"])
            before = printer.http.file_metadata.call_count
            printer.status()
            self.assertEqual(printer.http.file_metadata.call_count, before)
            for section, field, value in [("print_stats", "filename", "second.gcode"),
                                          ("print_stats", "total_duration", 1),
                                          ("virtual_sdcard", "file_position", 1),
                                          ("virtual_sdcard", "file_size", 2000)]:
                before = printer.http.file_metadata.call_count
                status[section][field] = value
                self.assertIsNone(printer.status()["stats"]["planned_total"])
                self.assertEqual(printer.http.file_metadata.call_count, before + 1)
                printer.status()
                self.assertEqual(printer.http.file_metadata.call_count, before + 1)
            # Same-name reprint after completion fetches immediately, even after failure.
            status["print_stats"]["state"] = "complete"
            printer.status()
            status["print_stats"]["state"] = "printing"
            before = printer.http.file_metadata.call_count
            printer.status()
            self.assertEqual(printer.http.file_metadata.call_count, before + 1)
            clock.return_value = 190
            printer.http.file_metadata.side_effect = None
            printer.http.file_metadata.return_value = []
            self.assertIsNone(printer.status()["stats"]["planned_total"])
            before = printer.http.file_metadata.call_count
            printer.status()
            self.assertEqual(printer.http.file_metadata.call_count, before)
            clock.return_value = 220
            printer.http.file_metadata.return_value = {"slicer": "OrcaSlicer", "estimated_time": 2081}
            self.assertEqual(printer.status()["stats"]["planned_total"], 2081)

    def test_metadata_size_must_match_the_active_reader_when_available(self):
        printer = plugin.Printer("http://printer.local")
        printer.http.query_objects = Mock(return_value=self.estimate_status())
        printer.http.file_metadata = Mock(return_value={"slicer": "OrcaSlicer", "estimated_time": 2081,
                                                        "size": 2000})
        self.assertIsNone(printer.status()["stats"]["planned_total"])
        printer.metadata_retry_at = 0
        printer.http.file_metadata.return_value["size"] = 1000
        self.assertAlmostEqual(printer.status()["stats"]["remaining"], 1331.84)

    def test_file_metadata_encodes_filename_and_uses_authenticated_transport(self):
        client = plugin.HttpClient("http://printer.local", "secret")
        with patch.object(client, "json", return_value={"result": {"estimated_time": 2081}}) as request:
            self.assertEqual(client.file_metadata("folder/a & b.gcode"), {"estimated_time": 2081})
            request.assert_called_once_with("/server/files/metadata?filename=folder%2Fa%20%26%20b.gcode", None)

    def test_print_metrics_follow_active_physical_tool(self):
        status = {"print_stats": {"state": "printing", "total_duration": 4200,
                                  "print_duration": 3600,
                                  "info": {"current_layer": 100, "total_layer": 200}},
                  "display_status": {"progress": .5}, "toolhead": {"extruder": "extruder2"},
                  "print_task_config": {"filament_type": ["PLA", "PLA", "PETG", "ABS"],
                                        "filament_color_rgba": ["FFFFFFFF", "FFFFFFFF", "12AB34FF"]},
                  "extruder2": {"temperature": 235, "target": 240},
                  "heater_bed": {"temperature": 70, "target": 75}}
        result = plugin.print_metrics(status)
        self.assertEqual((result["tool"], result["material"], result["color"]), (2, "PETG", "#12AB34"))
        self.assertEqual((result["elapsed"], result["remaining"]), (4200, None))
        self.assertEqual(result["hotend"]["temperature"], 235)
        self.assertEqual(result["current_layer"], 100)
        status["display_status"]["progress"] = 0
        self.assertIsNone(plugin.print_metrics(status)["remaining"])
        status["print_stats"]["state"] = "complete"
        self.assertEqual(plugin.print_metrics(status)["progress"], 1)
        self.assertEqual(plugin.print_metrics(status)["remaining"], 0)

    def test_print_metrics_missing_or_invalid_values_remain_unknown(self):
        result = plugin.print_metrics({"display_status": {"progress": float("nan")},
                                       "virtual_sdcard": {"progress": .25},
                                       "toolhead": {"extruder": "unknown"},
                                       "print_stats": {"total_duration": -1}})
        self.assertEqual(result["progress"], .25)
        self.assertIsNone(result["elapsed"])
        self.assertIsNone(result["tool"])
        self.assertEqual(result["hotend"], {})

    def test_client_rejects_api_errors_and_escapes_websocket_tokens(self):
        client = plugin.HttpClient("https://printer.local:7125", "secret")
        with patch.object(client, "json", return_value={"error": {"message": "secret"}}):
            with self.assertRaisesRegex(RuntimeError, "Printer rejected the request"):
                client.query_objects(plugin.ObjectQuery("print_stats"))
        with patch.object(client, "json", return_value={"result": "a&b?c/#"}):
            self.assertEqual(client.websocket_url(), "wss://printer.local:7125/websocket?token=a%26b%3Fc%2F%23")
        self.assertEqual(plugin.HttpClient("http://printer.local").websocket_url(),
                         "ws://printer.local/websocket")


def eventually(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate(), "Worker did not reach expected state"


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.clients = []
        self.messages = []

        def factory(*target):
            client = Mock()
            client.target = target
            client.status.return_value = {"printer": "standby", "light": False}
            client.snapshot.return_value = {"image": "frame"}
            self.clients.append(client)
            return client

        self.monitor = plugin.Monitor(self.messages.append, factory)
        self.addCleanup(self.monitor.close)

    def test_pause_resume_target_change_and_unload(self):
        self.monitor.update(("first", ""), playing=True)
        eventually(lambda: any("image" in message for message in self.messages))
        first = self.clients[0]
        self.monitor.update(None)
        eventually(lambda: first.close.called)
        count = len(self.messages)
        time.sleep(0.05)
        self.assertEqual(len(self.messages), count)
        generation = self.monitor.update(("second", ""), True, playing=True)
        eventually(lambda: len(self.clients) == 2 and self.clients[1].snapshot.called)
        self.assertEqual(self.clients[1].target, ("second", ""))
        self.clients[1].set_light.assert_called_once_with(True)
        self.assertEqual(self.messages[-1]["generation"], generation)
        self.monitor.close()
        self.assertFalse(self.monitor.thread.is_alive())

    def test_expired_lease_stops_client(self):
        self.monitor.update(("printer", ""), playing=True)
        eventually(lambda: bool(self.clients))
        with self.monitor.condition:
            self.monitor.expires = 0
            self.monitor.condition.notify()
        eventually(lambda: self.clients[0].close.called)

    def test_no_old_frame_after_target_change(self):
        entered, release = threading.Event(), threading.Event()

        def factory(*target):
            client = Mock()
            client.status.return_value = {}
            def snapshot():
                if target[0] == "old":
                    entered.set()
                    release.wait(timeout=2)
                return {"image": target[0]}
            client.snapshot.side_effect = snapshot
            return client

        self.monitor.client_factory = factory
        self.monitor.update(("old", ""), playing=True)
        self.assertTrue(entered.wait(timeout=2))
        self.monitor.update(("new", ""), playing=True)
        release.set()
        eventually(lambda: any(m.get("image") == "new" for m in self.messages))
        self.assertFalse(any(m.get("image") == "old" for m in self.messages))

    def test_errors_are_visible_and_secret_urls_are_not(self):
        def fail(*target):
            raise OSError("http://printer?token=secret")
        self.monitor.client_factory = fail
        self.monitor.update(("printer", ""))
        eventually(lambda: any(m.get("failed") for m in self.messages))
        self.assertTrue(self.messages[-1]["failed"])
        self.assertNotIn("secret", str(self.messages))

    def test_frames_wait_for_display_acknowledgement(self):
        generation = self.monitor.update(("printer", ""), playing=True)
        eventually(lambda: any("image" in m for m in self.messages))
        client = self.clients[0]
        self.monitor.update(("printer", ""), playing=True)
        time.sleep(0.05)
        self.assertEqual(client.snapshot.call_count, 1)
        self.monitor.frame_loaded(generation - 1)
        time.sleep(0.05)
        self.assertEqual(client.snapshot.call_count, 1)
        self.monitor.frame_loaded(generation)
        eventually(lambda: client.snapshot.call_count == 2)

    def test_generation_is_announced_before_frames_on_play_and_target_change(self):
        announced = set()
        displayed = []

        def display(message):
            self.messages.append(message)
            if message.get("type") == "target":
                announced.add(message["generation"])
            elif "image" in message and message["generation"] in announced:
                displayed.append(message)
                # Model the page: only frames from a known generation are acknowledged.
                if sum(m["generation"] == message["generation"] for m in displayed) == 1:
                    self.monitor.frame_loaded(message["generation"])

        self.monitor.publish = display
        for target, playing in [(("first", ""), False), (("first", ""), True),
                                (("second", ""), True)]:
            generation = self.monitor.update(target, playing=playing)
            self.assertIn(generation, announced)
            if playing:
                eventually(lambda: sum(m["generation"] == generation for m in displayed) == 2)

    def test_light_works_without_camera_playback(self):
        self.monitor.update(("printer", ""), True, playing=False)
        eventually(lambda: any("light" in m for m in self.messages))
        client = self.clients[0]
        client.set_light.assert_called_once_with(True)
        client.start_monitor.assert_not_called()
        client.snapshot.assert_not_called()

    def test_explicit_stop_closes_camera_but_keeps_status_available(self):
        generation = self.monitor.update(("printer", ""), playing=True)
        eventually(lambda: any("image" in m for m in self.messages))
        first = self.clients[0]
        self.monitor.update(("printer", ""), playing=False)
        self.monitor.frame_loaded(generation)
        eventually(lambda: len(self.clients) == 2 and self.clients[1].status.called)
        first.close.assert_called_once()
        self.clients[1].start_monitor.assert_not_called()
        self.clients[1].snapshot.assert_not_called()


class TargetTests(unittest.TestCase):
    def make_page(self, values, config=None, file=""):
        preset = Mock()
        preset.name = "Custom printer"
        preset.file = file
        preset.config_value.side_effect = values.get
        bundle = Mock()
        bundle.current_printer_preset.return_value = preset
        bundle.full_config_value.return_value = None
        page = plugin.U1Page()
        if config is None:
            config = {"allow_preset_connection": True}
        page.get_config = lambda: json.dumps(config)

        def save_config(value):
            config.clear()
            config.update(json.loads(value))
            return True

        page.save_config = Mock(side_effect=save_config)
        page.post_message = Mock()
        self.host = patch.object(orca, "host", types.SimpleNamespace(preset_bundle=lambda: bundle,
                                ui=types.SimpleNamespace(message=Mock(return_value="no"))), create=True)
        self.host.start()
        self.addCleanup(self.host.stop)
        return page

    def test_preset_connection_reads_require_consent_and_remember_the_decision(self):
        for choice in ("yes", "no", "cancel"):
            with self.subTest(choice=choice):
                page = self.make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local",
                                       "printhost_apikey": "secret"}, {})
                orca.host.ui.message.return_value = choice
                with patch.object(plugin, "saved_connection") as read_file:
                    for _ in range(2):
                        if choice == "yes":
                            self.assertEqual(page.selected_target(), ("http://printer.local", "secret"))
                        else:
                            with self.assertRaisesRegex(ValueError, "access is disabled"):
                                page.selected_target()
                    read_file.assert_not_called()
                orca.host.ui.message.assert_called_once()
                page.save_config.assert_called_once_with(json.dumps({
                    "allow_preset_connection": choice == "yes",
                }))
                if choice != "yes":
                    preset = orca.host.preset_bundle().current_printer_preset()
                    self.assertNotIn("print_host", [call.args[0] for call in preset.config_value.call_args_list])
                    self.assertNotIn("printhost_apikey", [call.args[0] for call in preset.config_value.call_args_list])

    def test_saved_denial_skips_prompt_and_file_read(self):
        page = self.make_page({"printer_model": "Snapmaker U1"}, {"allow_preset_connection": False})
        with patch.object(plugin, "saved_connection") as read_file:
            with self.assertRaisesRegex(ValueError, "access is disabled"):
                page.selected_target()
            read_file.assert_not_called()
        orca.host.ui.message.assert_not_called()

    def test_cleared_saved_approval_does_not_fall_back_to_cached_yes(self):
        config = {}
        page = self.make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"}, config)
        orca.host.ui.message.return_value = "yes"
        self.assertEqual(page.selected_target(), ("http://printer.local", ""))
        config.clear()
        orca.host.ui.message.return_value = "no"
        with self.assertRaisesRegex(ValueError, "access is disabled"):
            page.selected_target()
        self.assertEqual(orca.host.ui.message.call_count, 2)

    def test_one_approval_covers_future_u1_preset_selections(self):
        values = {"printer_model": "Snapmaker U1", "print_host": "first.local", "printhost_apikey": "first-key"}
        page = self.make_page(values, {}, file="/profiles/first.json")
        orca.host.ui.message.return_value = "yes"
        self.assertEqual(page.selected_target(), ("http://first.local", "first-key"))
        preset = orca.host.preset_bundle().current_printer_preset()
        preset.file = "/profiles/second.json"
        values.update(print_host="second.local", printhost_apikey="second-key")
        page.on_lifecycle_event(orca.LifecycleEvent.PresetSelected, types.SimpleNamespace(msg="printer"))
        self.assertEqual(page.selected_target(), ("http://second.local", "second-key"))
        orca.host.ui.message.assert_called_once()

    def test_revocation_blocks_connection_reads_after_selection(self):
        config = {}
        page = self.make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local",
                               "printhost_apikey": "key"}, config)
        orca.host.ui.message.return_value = "yes"
        page.selected_target()
        config["allow_preset_connection"] = False
        preset = orca.host.preset_bundle().current_printer_preset()
        preset.config_value.reset_mock()
        page.monitor = Mock()
        page.on_lifecycle_event(orca.LifecycleEvent.PresetSelected, types.SimpleNamespace(msg="printer"))
        with patch.object(plugin, "saved_connection") as read_file:
            page.on_message({"type": "activity", "active": True, "playing": True})
            read_file.assert_not_called()
        page.monitor.update.assert_called_with(None)
        self.assertNotIn("print_host", [call.args[0] for call in preset.config_value.call_args_list])
        self.assertNotIn("printhost_apikey", [call.args[0] for call in preset.config_value.call_args_list])

    def test_existing_global_approval_does_not_prompt_again(self):
        page = self.make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"},
                              {"allow_preset_connection": True})
        self.assertEqual(page.selected_target(), ("http://printer.local", ""))
        orca.host.ui.message.assert_not_called()

    def test_failed_permission_save_does_not_allow_connection_reads(self):
        page = self.make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"}, {})
        page.save_config.side_effect = None
        page.save_config.return_value = False
        orca.host.ui.message.return_value = "yes"
        with self.assertRaisesRegex(ValueError, "save.*permission"):
            page.selected_target()
        preset = orca.host.preset_bundle().current_printer_preset()
        self.assertNotIn("print_host", [call.args[0] for call in preset.config_value.call_args_list])

    def test_saved_approval_must_be_visible_in_effective_config(self):
        page = self.make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"}, {})
        # A preset override can hide a successful global save from get_config().
        page.get_config = lambda: "{}"
        orca.host.ui.message.return_value = "yes"
        with self.assertRaisesRegex(ValueError, "save.*permission"):
            page.selected_target()
        with self.assertRaisesRegex(ValueError, "access is disabled"):
            page.selected_target()
        orca.host.ui.message.assert_called_once()
        preset = orca.host.preset_bundle().current_printer_preset()
        self.assertNotIn("print_host", [call.args[0] for call in preset.config_value.call_args_list])

    def test_selection_change_during_consent_cannot_mix_connections(self):
        values = {"printer_model": "Snapmaker U1", "print_host": "first.local"}
        page = self.make_page(values, {}, file="/profiles/first.json")
        preset = orca.host.preset_bundle().current_printer_preset()

        def confirm(*args, **kwargs):
            preset.file = "/profiles/second.json"
            values.update(print_host="second.local", printhost_apikey="unapproved-key")
            return "yes"

        orca.host.ui.message.side_effect = confirm
        with self.assertRaisesRegex(ValueError, "selection changed"):
            page.selected_target()
        self.assertNotIn("print_host", [call.args[0] for call in preset.config_value.call_args_list])
        self.assertNotIn("printhost_apikey", [call.args[0] for call in preset.config_value.call_args_list])

    def test_disabling_access_during_consent_cannot_be_overwritten_by_yes(self):
        config = {}
        page = self.make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"}, config)

        def confirm(*args, **kwargs):
            config["allow_preset_connection"] = False
            return "yes"

        orca.host.ui.message.side_effect = confirm
        with self.assertRaisesRegex(ValueError, "access is disabled"):
            page.selected_target()
        page.save_config.assert_not_called()
        preset = orca.host.preset_bundle().current_printer_preset()
        self.assertNotIn("print_host", [call.args[0] for call in preset.config_value.call_args_list])

    def test_pending_consent_blocks_reentrant_page_messages(self):
        page = self.make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"}, {})

        def confirm(*args, **kwargs):
            with self.assertRaisesRegex(ValueError, "access is disabled"):
                page.selected_target()
            return "yes"

        orca.host.ui.message.side_effect = confirm
        self.assertEqual(page.selected_target(), ("http://printer.local", ""))
        orca.host.ui.message.assert_called_once()

    def test_consent_covers_the_selected_file_fallback(self):
        page = self.make_page({"printer_model": "Snapmaker U1"}, {})
        orca.host.ui.message.return_value = "yes"
        with patch.object(plugin, "saved_connection", return_value=("printer.local", "key")) as read_file:
            self.assertEqual(page.selected_target(), ("http://printer.local", "key"))
            read_file.assert_called_once()
        orca.host.ui.message.assert_called_once()

    def test_selected_preset_and_manual_host_credential_isolation(self):
        page = self.make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local",
                               "printhost_apikey": "secret"})
        self.assertEqual(page.selected_target(), ("http://printer.local", "secret"))
        page.get_config = lambda: '{"host":"http://other.local"}'
        self.assertEqual(page.selected_target(), ("http://other.local", ""))

    def test_saved_selected_preset_supplies_address_and_its_own_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selected.json"
            path.write_text(json.dumps({"print_host": "saved-printer.local:7125", "printhost_apikey": "saved-key"}))
            page = self.make_page({"printer_model": "Snapmaker U1", "printhost_apikey": "unrelated-key"}, file=str(path))
            self.assertEqual(page.selected_target(), ("http://saved-printer.local:7125", "saved-key"))

    def test_manual_host_works_with_other_presets_without_reading_their_credentials(self):
        page = self.make_page({"printer_model": "Bambu Lab A1", "printhost_apikey": "unrelated-key"},
                              {"host": "u1.local"})
        self.assertEqual(page.selected_target(), ("http://u1.local", ""))
        with patch.object(orca.host, "preset_bundle", side_effect=RuntimeError("No preset available")):
            self.assertEqual(page.selected_target(), ("http://u1.local", ""))
        page.monitor = Mock()
        page.on_message({"type": "activity", "active": True, "playing": True})
        page.monitor.update.assert_called_once_with(("http://u1.local", ""), None, True)
        page.get_config = lambda: '{"host":"file:///tmp/invalid"}'
        with self.assertRaises(ValueError):
            page.selected_target()

    def test_other_printer_without_manual_host_reports_address_warning(self):
        page = self.make_page({"printer_model": "Bambu Lab A1", "print_host": "other-printer.local"})
        page.monitor = Mock()
        for message in [{"type": "activity", "active": True, "playing": True},
                        {"type": "light", "enabled": True}]:
            page.on_message(message)
            page.monitor.update.assert_called_with(None)
            self.assertFalse(page.post_message.call_args.args[0]["supported"])
            self.assertTrue(page.post_message.call_args.args[0]["error"],
                            "An unavailable target includes an explanation")

    def test_u1_without_address_reports_configuration_warning(self):
        page = self.make_page({"printer_model": "Snapmaker U1"})
        page.monitor = Mock()
        page.on_message({"type": "activity", "active": True, "playing": True})
        page.monitor.update.assert_called_once_with(None)
        self.assertTrue(page.post_message.call_args.args[0]["supported"])
        self.assertTrue(page.post_message.call_args.args[0]["error"],
                            "An unavailable target includes an explanation")

    def test_inherited_u1_model_and_similar_names(self):
        preset = Mock()
        preset.name = "Quiet printer"
        preset.config_value.side_effect = {"inherits": "Snapmaker U1 (0.4 nozzle)"}.get
        self.assertTrue(plugin.is_u1_preset(preset))
        preset.config_value.side_effect = {"printer_model": "Snapmaker U10"}.get
        self.assertFalse(plugin.is_u1_preset(preset))
        preset.name = "Snapmaker U1 copy"
        preset.config_value.side_effect = {"printer_model": "Other printer"}.get
        self.assertFalse(plugin.is_u1_preset(preset))

    def test_printer_selection_stops_monitor_and_refreshes_page(self):
        page = plugin.U1Page()
        page.monitor = Mock()
        page.post_message = Mock()
        page.on_lifecycle_event(orca.LifecycleEvent.PresetSelected, types.SimpleNamespace(msg="printer"))
        page.monitor.update.assert_called_once_with(None)
        page.post_message.assert_called_once_with({"type": "selection_changed"})
        page.monitor.reset_mock()
        page.on_lifecycle_event(orca.LifecycleEvent.PresetSelected, types.SimpleNamespace(msg="filament"))
        page.monitor.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
