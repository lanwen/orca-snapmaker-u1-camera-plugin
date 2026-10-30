"""Client protocol and worker tests; no Orca instance or physical printer needed."""
import importlib.util
import json
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
        frame = plugin.Printer(self.address, "secret").snapshot()
        self.assertIn("Stale", frame["camera"])
        self.assertTrue(frame["image"].startswith("data:image/jpeg;base64,"))
        self.assertIn("cacheBust=", Endpoint.requests[-1][0])
        self.assertEqual(Endpoint.requests[-1][1], "secret")

    def test_fps_counts_new_images_instead_of_downloads(self):
        printer = plugin.Printer(self.address)
        printer.http.camera_snapshot = Mock(side_effect=[plugin.CameraSnapshot(b'\xff\xd8first', 1_000_000_000),
                                        plugin.CameraSnapshot(b'\xff\xd8first', 1_000_000_000),
                                        plugin.CameraSnapshot(b'\xff\xd8second', 1_000_000_000)])
        with patch.object(plugin.time, "monotonic", side_effect=[10, 10.1, 10.2]), \
                patch.object(plugin.time, "time", return_value=1_000_000_000):
            self.assertEqual(printer.snapshot()["camera"], "Live · 0.0 FPS")
            self.assertEqual(printer.snapshot()["camera"], "Live · 0.0 FPS")
            self.assertEqual(printer.snapshot()["camera"], "Live · 5.0 FPS")

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
        self.assertEqual(plugin.Printer(self.address).status(), {"printer": "standby", "light": True})

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
        page.get_config = lambda: json.dumps(config or {})
        page.post_message = Mock()
        self.host = patch.object(orca, "host", types.SimpleNamespace(preset_bundle=lambda: bundle), create=True)
        self.host.start()
        self.addCleanup(self.host.stop)
        return page

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
            self.assertIn('set "host" in plugin Config', page.post_message.call_args.args[0]["error"])

    def test_u1_without_address_reports_configuration_warning(self):
        page = self.make_page({"printer_model": "Snapmaker U1"})
        page.monitor = Mock()
        page.on_message({"type": "activity", "active": True, "playing": True})
        page.monitor.update.assert_called_once_with(None)
        self.assertTrue(page.post_message.call_args.args[0]["supported"])
        self.assertIn('set "host" in plugin Config', page.post_message.call_args.args[0]["error"])

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
