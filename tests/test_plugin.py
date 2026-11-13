"""Client protocol and worker tests; no Orca instance or physical printer needed."""
import base64
import hashlib
import importlib.util
import json
import re
import socket
import struct
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


class WebSocketServer(ThreadingHTTPServer):
    def get_request(self):
        connection = super().get_request()
        self.connections += 1
        return connection


class WebSocketEndpoint(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        if self.server.redirect_status:
            self.send_response(self.server.redirect_status)
            self.send_header("Location", self.server.redirect_url)
            self.end_headers()
            return
        accept = base64.b64encode(hashlib.sha1(
            (self.headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
        ).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        reply = json.dumps({"id": 1, "result": {}})
        self.wfile.write(plugin.websocket.ABNF(
            fin=1, opcode=plugin.websocket.ABNF.OPCODE_TEXT, mask_value=0, data=reply).format())
        self.wfile.flush()
        # Keep the upgraded connection open until the client closes it.
        while self.rfile.read(1):
            pass

    def log_message(self, *args):
        pass


class WebSocketRedirectTests(unittest.TestCase):
    def setUp(self):
        self.servers = []

    def tearDown(self):
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join()

    def serve(self, redirect_status=0, redirect_url=""):
        server = WebSocketServer(("127.0.0.1", 0), WebSocketEndpoint)
        server.connections = 0
        server.requests = []
        server.redirect_status = redirect_status
        server.redirect_url = redirect_url
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return server

    def test_real_client_rejects_every_redirect_without_contacting_target(self):
        token = "private-oneshot-token"
        target = self.serve()
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                source = self.serve(status, "ws://127.0.0.1:%d/redirected?token=%s" % (
                    target.server_port, token))
                client = plugin.HttpClient("http://127.0.0.1:%d" % source.server_port, "api-key")
                try:
                    with patch.object(client, "_query", return_value=token), \
                            self.assertRaises(plugin.websocket.WebSocketException) as raised:
                        client.start_monitor()
                    self.assertNotIn(token, str(raised.exception))
                    self.assertEqual(source.requests, ["/websocket?token=" + token])
                    self.assertEqual(target.requests, [])
                    self.assertEqual(target.connections, 0, "Redirect destinations must never be contacted")
                    self.assertIsNone(client.ws)
                finally:
                    client.close()

    def test_real_client_still_accepts_normal_handshake(self):
        server = self.serve()
        client = plugin.HttpClient("http://127.0.0.1:%d" % server.server_port)
        try:
            client.start_monitor()
            self.assertTrue(client.ws.connected)
            self.assertEqual(server.requests, ["/websocket"])
        finally:
            client.close()


class WebSocketLimitTests(unittest.TestCase):
    def make_socket(self, wire):
        ws = plugin.CameraWebSocket()
        pending = bytearray(wire)
        reads = []

        def receive(size):
            reads.append(size)
            if size > len(pending):
                self.fail("Payload read exceeded the supplied frame headers")
            data = bytes(pending[:size])
            del pending[:size]
            return data

        ws.frame_buffer.recv = receive
        ws.pong = Mock()
        ws.shutdown = Mock()
        return ws, reads, pending

    def frame(self, payload, opcode=1, fin=True):
        return bytes([(0x80 if fin else 0) | opcode, len(payload)]) + payload

    def test_oversized_lengths_are_rejected_before_payload_reads(self):
        for header in [b'\x81\x7e' + struct.pack('!H', 513),
                       b'\x82\x7f' + struct.pack('!Q', 2 ** 40),
                       b'\x81\xff' + struct.pack('!Q', 2 ** 40)]:
            with self.subTest(header=header), \
                    patch.object(plugin._CameraFrameBuffer, "RESPONSE_LIMIT", 512):
                ws, reads, pending = self.make_socket(header)
                with self.assertRaisesRegex(RuntimeError, "too large"):
                    ws.recv()
                self.assertEqual(reads, [2, len(header) - 2])
                self.assertFalse(pending)
                ws.shutdown.assert_called_once()

    def test_fragment_total_rejected_before_next_payload_even_with_ping(self):
        wire = (self.frame(b'12345', fin=False) + self.frame(b'ping', opcode=9)
                + bytes([0x80, 4]))
        with patch.object(plugin._CameraFrameBuffer, "RESPONSE_LIMIT", 8):
            ws, reads, pending = self.make_socket(wire)
            with self.assertRaisesRegex(RuntimeError, "too large"):
                ws.recv()
        self.assertEqual(reads, [2, 5, 2, 4, 2])
        self.assertFalse(pending)
        ws.pong.assert_called_once_with(b'ping')
        ws.shutdown.assert_called_once()
        self.assertEqual(ws.cont_frame.cont_data[1], b'12345')

    def test_exact_byte_limit_and_next_message_survive_control_frames(self):
        # Four UTF-8 characters occupy eight bytes on the wire.
        data = "éééé".encode()
        wire = (self.frame(data[:4], fin=False) + self.frame(b'ping', opcode=9)
                + self.frame(data[4:], opcode=0) + self.frame(b'12345678'))
        with patch.object(plugin._CameraFrameBuffer, "RESPONSE_LIMIT", 8):
            ws, _, pending = self.make_socket(wire)
            self.assertEqual(ws.recv(), "éééé")
            self.assertEqual(ws.recv(), "12345678")
        self.assertFalse(pending)
        ws.pong.assert_called_once_with(b'ping')
        ws.shutdown.assert_not_called()

    def test_fragment_limit_counts_utf8_bytes(self):
        wire = self.frame("ééé".encode(), fin=False) + bytes([0x80, 4])
        with patch.object(plugin._CameraFrameBuffer, "RESPONSE_LIMIT", 8):
            ws, reads, _ = self.make_socket(wire)
            with self.assertRaisesRegex(RuntimeError, "too large"):
                ws.recv()
        self.assertEqual(reads, [2, 6, 2])


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
        self.assertTrue(frame["frozen"])
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
            displayed_rates = [printer.snapshot()["fps"]
                               for _ in range(3)]
            self.assertEqual(displayed_rates, [0, 0, 5],
                             "Downloading an identical image must not increase the displayed frame rate")

    def test_stream_freshness_uses_timestamp_instead_of_identical_image_content(self):
        printer = plugin.Printer(self.address)
        printer.http.camera_snapshot = Mock(return_value=plugin.CameraSnapshot(b'\xff\xd8still', 100))
        with patch.object(plugin.time, "time", return_value=105):
            self.assertFalse(printer.snapshot()["frozen"])
        with patch.object(plugin.time, "time", return_value=106):
            self.assertTrue(printer.snapshot()["frozen"])
        printer.http.camera_snapshot.return_value = plugin.CameraSnapshot(b'\xff\xd8still', None)
        self.assertFalse(printer.snapshot()["frozen"])

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
        self.assertEqual(connect.call_args.kwargs["redirect_limit"], 0)
        self.assertIs(connect.call_args.kwargs["class_"], plugin.CameraWebSocket)

    def test_camera_failure_replaces_socket_and_preserves_status(self):
        for failure in (RuntimeError("rejected"), OSError("disconnected")):
            with self.subTest(failure=failure):
                printer = plugin.Printer(self.address)
                self.addCleanup(printer.close)
                failed, recovered = Mock(), Mock()
                if isinstance(failure, RuntimeError):
                    failed.recv.return_value = json.dumps({"id": 1, "error": {"message": "denied"}})
                else:
                    failed.recv.side_effect = failure
                recovered.recv.return_value = json.dumps({"id": 2, "result": {}})
                sockets = iter((failed, recovered))

                def connect_camera(url, **kwargs):
                    ws = next(sockets)
                    ws.sock = kwargs["socket"]
                    ws.close.side_effect = lambda timeout: ws.sock.close()
                    return ws

                with patch.object(plugin.websocket, "create_connection", side_effect=connect_camera) as connect:
                    with self.assertRaises(type(failure)):
                        printer.start_monitor()
                    self.assertIsNone(printer.http.ws)
                    failed.close.assert_called_once_with(timeout=0)
                    self.assertEqual(printer.status()["printer"], "standby")
                    printer.start_monitor()
                self.assertEqual(connect.call_count, 2)
                self.assertIs(printer.http.ws, recovered)

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

    def test_monitor_renewal_resets_send_timeout_after_late_ack(self):
        client = plugin.HttpClient(self.address)
        ws = Mock()
        ws.timeout = 2
        ws.settimeout.side_effect = lambda timeout: setattr(ws, 'timeout', timeout)
        send_timeouts = []
        ws.send.side_effect = lambda _: send_timeouts.append(ws.timeout)
        ws.recv.side_effect = [json.dumps({"id": 1, "result": {}}),
                               json.dumps({"id": 2, "result": {}})]
        with patch.object(plugin.websocket, "create_connection", return_value=ws):
            client.start_monitor()
            ws.settimeout(.001)
            client.start_monitor()
        self.assertTrue(all(1 < timeout <= 2 for timeout in send_timeouts))
        client.close()

    def test_monitor_renewal_reads_ack_after_queued_notifications(self):
        client = plugin.HttpClient(self.address)
        ws = Mock()
        ws.recv.side_effect = [json.dumps({"id": 1, "result": {}})] + [
            json.dumps({"method": "notify_status_update", "params": [{}, 1]})
        ] * 100 + [json.dumps({"id": 2, "result": {}})]
        with patch.object(plugin.websocket, "create_connection", return_value=ws), \
                patch.object(plugin, "NETWORK_TIMEOUT", 2):
            client.start_monitor()
            client.start_monitor()
        self.assertEqual(ws.recv.call_count, 102)
        client.close()

    def test_monitor_limits_responses(self):
        client = plugin.HttpClient(self.address)
        ws = Mock()
        ws.recv.return_value = 'x' * 512_001
        with patch.object(plugin.websocket, "create_connection", return_value=ws), \
                self.assertRaisesRegex(RuntimeError, 'too large'):
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
            for name in ("fan_generic cavity_fan", "fan", "fan_generic e1_fan", "fan_generic e2_fan", "fan_generic e3_fan", "heater_fan power_fan", *("heater_fan e%d_nozzle_fan" % tool for tool in range(4))):
                self.assertIn(plugin.ObjectQuery(name, ("speed", "rpm")), queries)
            self.assertIn(plugin.ObjectQuery("purifier", ("power_detected", "exhaust_fan", "inner_fan", "inner_fan_rpm")), queries)
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

    def test_fans_follow_connected_tool_and_reject_missing_or_invalid_readings(self):
        status = {"fan_generic cavity_fan": {"speed": .7, "rpm": 3200}}
        for tool, name in enumerate(("fan", "fan_generic e1_fan", "fan_generic e2_fan", "fan_generic e3_fan")):
            head = "extruder" + (str(tool) if tool else "")
            status.update(toolhead={"extruder": head})
            status[head] = {"state": "ACTIVATE"}
            status[name] = {"speed": .5, "rpm": 8100}
            status["heater_fan e%d_nozzle_fan" % tool] = {"speed": .75, "rpm": 16761}
            result = plugin.print_metrics(status)
            self.assertEqual(result["aux_fan"], {"speed": .7, "rpm": 3200})
            self.assertEqual(result["part_fan"], {"speed": .5, "rpm": 8100})
            self.assertEqual(result["hotend_fan"], {"speed": .75, "rpm": 16761})
            status[head]["state"] = "PARKED"
        self.assertEqual(plugin.print_metrics(status)["part_fan"], {"speed": None, "rpm": None})
        for speed, rpm in ((None, None), (True, False), (-1, -1), (1.1, float("nan"))):
            status["fan_generic cavity_fan"] = {"speed": speed, "rpm": rpm}
            self.assertEqual(plugin.print_metrics(status)["aux_fan"], {"speed": None, "rpm": None})
        status["fan_generic cavity_fan"] = {"speed": 0, "rpm": 0}
        self.assertEqual(plugin.print_metrics(status)["aux_fan"], {"speed": 0, "rpm": 0})

    def test_additional_fans_require_current_valid_status_and_connected_cover(self):
        status = {"heater_fan power_fan": {"speed": .6, "rpm": None},
                  "purifier": {"power_detected": True, "exhaust_fan": {"speed": .6},
                               "inner_fan": {"speed": 0}, "inner_fan_rpm": 0}}
        result = plugin.print_metrics(status)
        self.assertEqual(result["psu_fan"], {"speed": .6, "rpm": None})
        self.assertEqual(result["exhaust_fan"], {"speed": .6, "rpm": None})
        self.assertEqual(result["circulation_fan"], {"speed": 0, "rpm": 0})
        self.assertEqual(result["hotend_fan"], {"speed": None, "rpm": None})
        for power in (False, None, 1):
            status["purifier"]["power_detected"] = power
            result = plugin.print_metrics(status)
            for name in ("exhaust_fan", "circulation_fan"):
                self.assertEqual(result[name], {"speed": None, "rpm": None})
        self.assertEqual(plugin.print_metrics({})["psu_fan"], {"speed": None, "rpm": None})

    def test_four_toolheads_use_physical_status_and_live_nozzle_sizes(self):
        status = {"toolhead": {"extruder": "extruder"},
                  "extruder": {"state": "PARKED", "temperature": 25, "target": 0, "nozzle_diameter": .4},
                  "extruder1": {"state": "ACTIVATE", "temperature": 180, "target": 240, "nozzle_diameter": .6},
                  "extruder2": {"state": "UNKNOWN", "nozzle_diameter": float("nan")},
                  "print_task_config": {"filament_type": ["PLA", "PETG", "NONE"],
                                        "filament_color_rgba": ["FFFFFFFF", "invalid"],
                                        "filament_exist": [True, False], "extruder_map_table": [1]}}
        result = plugin.print_metrics(status, {"slicer": "OrcaSlicer", "filament_name": "PETG profile", "layer_height": .2})
        self.assertEqual([tool["state"] for tool in result["tools"]], ["parked", "connected", "unknown", "unavailable"])
        self.assertEqual(result["tools"][1]["nozzle_diameter"], .6)
        self.assertEqual(result["tools"][1]["heater"], {"temperature": 180, "target": 240})
        self.assertIsNone(result["tools"][1]["color"])
        self.assertFalse(result["tools"][1]["filament_present"])
        self.assertEqual(result["tools"][1]["material_profile"], "PETG profile")
        self.assertIsNone(result["tools"][2]["material"])
        self.assertIsNone(result["tools"][2]["nozzle_diameter"])
        self.assertEqual(result["layer_height"], .2)
        status["extruder1"]["activating_move"] = True
        self.assertEqual(plugin.print_metrics(status)["tools"][1]["state"], "changing")
        empty = plugin.print_metrics({})
        self.assertEqual(len(empty["tools"]), 4)
        self.assertTrue(all(tool["state"] == "unavailable" for tool in empty["tools"]))
        self.assertIsNone(plugin.print_metrics({}, {"layer_height": -1})["layer_height"])

    def test_filament_details_follow_physical_tool_and_sliced_mapping(self):
        status = {"toolhead": {"extruder": "extruder2"},
                  "print_task_config": {"filament_type": ["PLA", "PLA", "PETG"],
                                        "filament_vendor": ["Snapmaker", "Generic", " Polymaker "],
                                        "filament_sub_type": ["SnapSpeed", "Basic", " HF "],
                                        "extruder_map_table": [2, 0]}}
        metadata = {"slicer": "OrcaSlicer", "filament_name": 'PETG profile";"PLA profile',
                    "filament_used_mm": [100, 200]}
        result = plugin.print_metrics(status, metadata)
        self.assertEqual((result["material_vendor"], result["material_subtype"], result["material_profile"]),
                         ("Polymaker", "HF", "PETG profile"))
        status["toolhead"]["extruder"] = "extruder"
        self.assertEqual(plugin.print_metrics(status, metadata)["material_profile"], "PLA profile")
        status["print_task_config"]["extruder_map_table"] = [0, 2]
        self.assertEqual(plugin.print_metrics(status, metadata)["material_profile"], "PETG profile")
        status["toolhead"]["extruder"] = "unknown"
        result = plugin.print_metrics(status, metadata)
        for key in ("material", "material_vendor", "material_subtype", "material_profile"):
            self.assertIsNone(result[key])

    def test_sliced_profiles_handle_quotes_and_hide_ambiguous_or_missing_details(self):
        status = {"toolhead": {"extruder": "extruder"},
                  "print_task_config": {"filament_type": ["PLA"], "filament_vendor": ["NONE"],
                                        "filament_sub_type": [42], "extruder_map_table": [0, 0]}}
        for raw, used, expected in [
            ('PLA profile;PETG profile', [100, 200], None),
            ('PLA profile;PLA profile', [100, 200], 'PLA profile'),
            ('PLA profile;Unused profile', [100, 0], 'PLA profile'),
            ('PLA profile;Unused profile', [100, 200, 300], None),
            ('Custom; PLA', [100], 'Custom; PLA'),
            ('Custom; PLA";"PETG profile', [100, 200], None),
            ('"PLA profile";"PLA profile"', [100, 200], 'PLA profile'),
            ('NONE', [100], None),
            ('', [100], None),
        ]:
            with self.subTest(raw=raw, used=used):
                metadata = {"slicer": "SnapmakerOrca", "filament_name": raw, "filament_used_mm": used}
                result = plugin.print_metrics(status, metadata)
                self.assertEqual(result["material_profile"], expected)
                self.assertIsNone(result["material_vendor"])
                self.assertIsNone(result["material_subtype"])
        metadata = {"slicer": "OrcaSlicer", "filament_name": 'PLA profile', "filament_used_mm": [100]}
        for mapping in (None, [], [True], ["0"], [1]):
            status["print_task_config"]["extruder_map_table"] = mapping
            self.assertIsNone(plugin.print_metrics(status, metadata)["material_profile"])

    def test_filament_profile_metadata_does_not_require_a_time_estimate(self):
        printer = plugin.Printer("http://printer.local")
        status = self.estimate_status()
        status.update({"toolhead": {"extruder": "extruder"},
                       "print_task_config": {"extruder_map_table": [0]}})
        printer.http.query_objects = Mock(return_value=status)
        printer.http.file_metadata = Mock(return_value={"slicer": "OrcaSlicer", "filament_name": "PLA profile"})
        result = printer.status()["stats"]
        self.assertEqual(result["material_profile"], "PLA profile")
        self.assertIsNone(result["planned_total"])
        fields = next(query.fields for query in printer.http.query_objects.call_args.args
                      if query.name == "print_task_config")
        self.assertTrue({"filament_vendor", "filament_sub_type", "extruder_map_table"}.issubset(fields))
        printer.http.file_metadata.return_value["size"] = 2000
        status["virtual_sdcard"]["file_size"] = 1000
        printer.metadata_retry_at = 0
        self.assertIsNone(printer.status()["stats"]["material_profile"])

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


class WorkerScheduleTests(unittest.TestCase):
    def test_camera_failures_preserve_status_cadence_and_recover(self):
        for failure in ("activation", "snapshot"):
            with self.subTest(failure=failure):
                now = [100.0]
                messages, status_times, camera_times = [], [], []
                client = Mock()

                def status():
                    self.assertFalse(client.close.called)
                    status_times.append(now[0])
                    return {"printer": "standby"}

                def camera():
                    camera_times.append(now[0])
                    if len(camera_times) <= 3:
                        raise OSError("http://printer?token=secret")
                    return {"image": "frame"}

                client.status.side_effect = status
                if failure == "activation":
                    client.start_monitor.side_effect = camera
                    client.snapshot.return_value = {"image": "frame"}
                else:
                    client.snapshot.side_effect = camera

                def wait(timeout):
                    now[0] += .25
                    monitor.update(("printer", ""), playing=True)
                    if now[0] >= 110:
                        monitor.closed = True

                with patch.object(plugin.threading.Thread, "start"), \
                        patch.object(plugin.time, "monotonic", side_effect=lambda: now[0]):
                    factory = Mock(return_value=client)
                    monitor = plugin.Monitor(messages.append, factory)
                    monitor.condition.wait = wait
                    monitor.update(("printer", ""), playing=True)
                    monitor.run()
                self.assertEqual(status_times, [100, 103, 106, 109])
                self.assertEqual(sum(m.get("printer") == "standby" for m in messages), 4)
                self.assertEqual(camera_times, [100, 103, 106, 109])
                self.assertTrue(any(m.get("image") == "frame" for m in messages))
                self.assertEqual(sum(m.get("failed", False) for m in messages), 3)
                self.assertNotIn("secret", str(messages))
                factory.assert_called_once()
                client.close.assert_called_once()

    def test_camera_activates_immediately_and_renews_after_sixty_seconds(self):
        now = [100]
        client = Mock()
        client.status.return_value = {}
        activations = []

        def snapshot():
            activations.append(client.start_monitor.call_count)
            if len(activations) == 3:
                monitor.closed = True
            else:
                now[0] = 159 if len(activations) == 1 else 160
                monitor.update(("printer", ""), playing=True)
                monitor.frame_ready = True
            return {"image": "frame"}

        client.snapshot.side_effect = snapshot
        with patch.object(plugin.threading.Thread, "start"), \
                patch.object(plugin.time, "monotonic", side_effect=lambda: now[0]):
            monitor = plugin.Monitor(Mock(), lambda *target: client)
            monitor.update(("printer", ""), playing=True)
            monitor.run()
        self.assertEqual(activations, [1, 1, 2])
        client.close.assert_called_once()


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

    def test_target_change_and_unload_interrupt_active_http(self):
        for operation, unload in ((operation, unload) for operation in
                                  ("status", "start_monitor", "snapshot") for unload in (False, True)):
            with self.subTest(operation=operation, unload=unload):
                local, peer = socket.socketpair()
                entered = threading.Event()
                http = plugin.HttpClient("http://printer")
                first, second = Mock(), Mock()
                first.close.side_effect = http.close
                first.status.return_value = {}
                getattr(first, operation).side_effect = lambda: http.request("/")
                second.status.return_value = {}
                clients = iter((first, second))
                monitor = plugin.Monitor(Mock(), lambda *args: next(clients))
                def connect(operation, *args):
                    entered.set()
                    return operation.track(local)
                try:
                    with patch.object(plugin.NetworkOperation, "connect", connect):
                        monitor.update(("first", ""), playing=True)
                        self.assertTrue(entered.wait(1))
                        started = time.monotonic()
                        if unload:
                            monitor.close()
                            self.assertFalse(monitor.thread.is_alive())
                        else:
                            monitor.update(("second", ""))
                            eventually(lambda: second.status.called)
                        self.assertLess(time.monotonic() - started, .7)
                finally:
                    monitor.close()
                    peer.close()
                first.close.assert_called_once()

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

    def test_light_failure_does_not_interrupt_camera_or_expose_exception_details(self):
        generation = self.monitor.update(("printer", ""), playing=True)
        eventually(lambda: any("image" in m for m in self.messages))
        client = self.clients[0]
        client.set_light.side_effect = OSError("http://printer?token=secret")
        self.monitor.update(("printer", ""), True, playing=True)
        eventually(lambda: any("light_error" in m for m in self.messages))
        self.monitor.frame_loaded(generation)
        eventually(lambda: client.snapshot.call_count == 2)
        self.assertFalse(any(m.get("failed") for m in self.messages))
        self.assertNotIn("secret", str(self.messages))
        self.assertFalse(client.close.called)

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
        with self.assertRaisesRegex(ValueError, "(?:save|confirm).*access choice"):
            page.selected_target()
        preset = orca.host.preset_bundle().current_printer_preset()
        self.assertNotIn("print_host", [call.args[0] for call in preset.config_value.call_args_list])

    def test_saved_approval_must_be_visible_in_effective_config(self):
        page = self.make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"}, {})
        # A preset override can hide a successful global save from get_config().
        page.get_config = lambda: "{}"
        orca.host.ui.message.return_value = "yes"
        with self.assertRaisesRegex(ValueError, "(?:save|confirm).*access choice"):
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
