"""Client protocol and worker tests; no Orca instance or physical printer needed."""
import base64
import hashlib
import importlib.util
import json
import re
import socket
import struct
from pathlib import Path
from queue import Queue
import sys
import threading
import time
import types
import pytest
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


orca = types.ModuleType("orca")
orca.pages = types.SimpleNamespace(PagesPluginCapabilityBase=object)
orca.base = object
orca.plugin = lambda cls: cls
orca.LifecycleEvent = types.SimpleNamespace(DeviceSelected="device", PresetSelected="preset", PresetSaved="saved")
sys.modules["orca"] = orca
spec = importlib.util.spec_from_file_location("plugin", Path(__file__).parents[1] / "dist" / "orca-snapmaker-u1-camera-plugin_any.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class TestPageIcon:
    def test_native_tab_icon_is_available_before_loading_page_html(self, tmp_path):
        directory = str(tmp_path)
        with patch.object(plugin, "__file__", str(Path(directory) / "camera.py")):
            page = plugin.U1Page()
            icon = Path(page.get_icon())
            assert icon.is_absolute()
            assert icon.suffix == '.svg'
            assert icon.parent == Path(directory)
            expected = (Path(__file__).parents[1] / "docs/branding/u1-logo.svg").read_text().replace("#249b7c", "#FEFEFE")
            assert icon.read_text() == expected
            assert page.get_icon() == str(icon)


class Endpoint(BaseHTTPRequestHandler):
    modified = "Thu, 01 Jan 1970 00:00:00 GMT"
    requests = []
    payloads = []
    status_code = 200
    status_body = None

    def do_GET(self):
        self.requests.append((self.path, self.headers.get("X-Api-Key")))
        self.send_response(self.status_code if self.path.startswith("/printer/objects/query?") else
                           302 if self.path == "/redirect" else 401 if self.path == "/denied" else 200)
        if self.path == "/redirect":
            self.send_header("Location", "http://example.com")
        self.send_header("Last-Modified", self.modified)
        self.end_headers()
        if self.path.startswith("/server/files"):
            self.wfile.write(b"\xff\xd8test-jpeg")
        elif self.path == "/large":
            self.wfile.write(b"123456")
        elif self.path.startswith("/printer/objects/query?") and self.status_body is not None:
            self.wfile.write(self.status_body)
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


class TestWebSocketRedirect:



    @pytest.mark.parametrize('status', [
        pytest.param(301, id='301'),
        pytest.param(302, id='302'),
        pytest.param(303, id='303'),
        pytest.param(307, id='307'),
        pytest.param(308, id='308'),
    ])
    def test_real_client_rejects_every_redirect_without_contacting_target(self, websocket_server, status):
        token = "private-oneshot-token"
        target = websocket_server()
        source = websocket_server(status, "ws://127.0.0.1:%d/redirected?token=%s" % (
            target.server_port, token))
        client = plugin.HttpClient("http://127.0.0.1:%d" % source.server_port, "api-key")
        try:
            with patch.object(client, "_query", return_value=token), \
                    pytest.raises(plugin.websocket.WebSocketException) as raised:
                client.start_monitor()
            assert token not in str(raised.value)
            assert source.requests == ['/websocket?token=' + token]
            assert target.requests == []
            assert target.connections == 0, 'Redirect destinations must never be contacted'
            assert client.ws is None
        finally:
            client.close()

    def test_real_client_still_accepts_normal_handshake(self, websocket_server):
        server = websocket_server()
        client = plugin.HttpClient("http://127.0.0.1:%d" % server.server_port)
        try:
            client.start_monitor()
            assert client.ws.connected
            assert server.requests == ['/websocket']
        finally:
            client.close()


class TestWebSocketLimit:
    def make_socket(self, wire):
        ws = plugin.CameraWebSocket()
        pending = bytearray(wire)
        reads = []

        def receive(size):
            reads.append(size)
            if size > len(pending):
                pytest.fail('Payload read exceeded the supplied frame headers')
            data = bytes(pending[:size])
            del pending[:size]
            return data

        ws.frame_buffer.recv = receive
        ws.pong = Mock()
        ws.shutdown = Mock()
        return ws, reads, pending

    def frame(self, payload, opcode=1, fin=True):
        return bytes([(0x80 if fin else 0) | opcode, len(payload)]) + payload

    @pytest.mark.parametrize('header', [
        pytest.param(b'\x81~' + struct.pack('!H', 513), id='oversized-short-frame'),
        pytest.param(b'\x82\x7f' + struct.pack('!Q', 2 ** 40), id='oversized-long-frame'),
        pytest.param(b'\x81\xff' + struct.pack('!Q', 2 ** 40), id='oversized-masked-frame'),
    ])
    def test_oversized_lengths_are_rejected_before_payload_reads(self, header):
        with patch.object(plugin._CameraFrameBuffer, 'RESPONSE_LIMIT', 512):
            ws, reads, pending = self.make_socket(header)
            with pytest.raises(RuntimeError, match='too large'):
                ws.recv()
            assert reads == [2, len(header) - 2]
            assert not pending
            ws.shutdown.assert_called_once()

    def test_fragment_total_rejected_before_next_payload_even_with_ping(self):
        wire = (self.frame(b'12345', fin=False) + self.frame(b'ping', opcode=9)
                + bytes([0x80, 4]))
        with patch.object(plugin._CameraFrameBuffer, "RESPONSE_LIMIT", 8):
            ws, reads, pending = self.make_socket(wire)
            with pytest.raises(RuntimeError, match='too large'):
                ws.recv()
        assert reads == [2, 5, 2, 4, 2]
        assert not pending
        ws.pong.assert_called_once_with(b'ping')
        ws.shutdown.assert_called_once()
        assert ws.cont_frame.cont_data[1] == b'12345'

    def test_exact_byte_limit_and_next_message_survive_control_frames(self):
        # Four UTF-8 characters occupy eight bytes on the wire.
        data = "éééé".encode()
        wire = (self.frame(data[:4], fin=False) + self.frame(b'ping', opcode=9)
                + self.frame(data[4:], opcode=0) + self.frame(b'12345678'))
        with patch.object(plugin._CameraFrameBuffer, "RESPONSE_LIMIT", 8):
            ws, _, pending = self.make_socket(wire)
            assert ws.recv() == 'éééé'
            assert ws.recv() == '12345678'
        assert not pending
        ws.pong.assert_called_once_with(b'ping')
        ws.shutdown.assert_not_called()

    def test_fragment_limit_counts_utf8_bytes(self):
        wire = self.frame("ééé".encode(), fin=False) + bytes([0x80, 4])
        with patch.object(plugin._CameraFrameBuffer, "RESPONSE_LIMIT", 8):
            ws, reads, _ = self.make_socket(wire)
            with pytest.raises(RuntimeError, match='too large'):
                ws.recv()
        assert reads == [2, 6, 2]


class TestClient:


    def status_message(self, printer):
        messages, arrived = [], threading.Event()

        def publish(message):
            if "stats" in message:
                messages.append(message)
                arrived.set()

        monitor = plugin.Monitor(publish, lambda *target: printer)
        try:
            monitor.update((printer.http.address, "secret"), playing=False)
            assert arrived.wait(1)
            return messages[0]
        finally:
            monitor.close()

    @pytest.mark.parametrize('code,body,category', [
        pytest.param(401, b'private-response-secret', 'access_denied', id='unauthorized'),
        pytest.param(403, b'private-response-secret', 'access_denied', id='forbidden'),
        pytest.param(500, b'private-response-secret', 'unreachable', id='server-error'),
        pytest.param(200, b'{"secret":', 'invalid_response', id='truncated-json'),
        pytest.param(200, b'\xffsecret', 'invalid_response', id='invalid-encoding'),
        pytest.param(200, b'[]', 'invalid_response', id='json-array'),
        pytest.param(200, b'{}', 'invalid_response', id='missing-result'),
        pytest.param(200, b'{"result":[]}', 'invalid_response', id='invalid-result'),
        pytest.param(200, b'{"result":{"status":[]}}', 'invalid_response', id='invalid-status'),
        pytest.param(200, b'{"result":{"status":{"print_stats":"secret"}}}', 'invalid_response', id='invalid-print-stats'),
        pytest.param(200, b'{"result":{"status":{"print_stats":{"info":["secret"]}}}}', 'invalid_response', id='invalid-layer-info'),
        pytest.param(200, b'x' * 512001, 'invalid_response', id='oversized-response'),
    ])
    def test_status_response_failures_use_safe_categories(self, http_endpoint, code, body, category):
        with patch.object(Endpoint, 'status_code', code), patch.object(Endpoint, 'status_body', body):
            message = self.status_message(plugin.Printer(http_endpoint, "secret"))
            assert message.get('status_error') == category
            assert message['stats'] is None
            assert message['light'] is None
            assert 'secret' not in str(message)
            assert http_endpoint not in str(message)

    def test_status_deadline_uses_timeout_category(self):
        local, peer = socket.socketpair()
        printer = plugin.Printer("http://printer")

        def connect(operation, *args):
            return operation.track(local)

        try:
            with patch.object(plugin.NetworkOperation, "connect", connect), \
                    patch.object(plugin, "NETWORK_TIMEOUT", .1):
                message = self.status_message(printer)
            assert message['status_error'] == 'timeout'
        finally:
            printer.close()
            peer.close()

    @pytest.mark.parametrize('error,category', [
        pytest.param(TimeoutError('url?token=secret'), 'timeout', id='deadline'),
        pytest.param(OSError('Authorization: secret'), 'unreachable', id='network'),
        pytest.param(RuntimeError('http://user:secret@printer'), 'unreachable', id='runtime-fallback'),
        pytest.param(Exception('private-response-secret'), 'unreachable', id='unexpected-fallback'),
    ])
    def test_status_timeout_network_and_unexpected_failures_are_safe(self, http_endpoint, error, category):
        printer = plugin.Printer(http_endpoint)
        printer.http.query_objects = Mock(side_effect=error)
        message = self.status_message(printer)
        assert message.get('status_error') == category
        assert 'secret' not in str(message)

    @pytest.mark.parametrize('value', [
        pytest.param('', id='empty'),
        pytest.param('file:///tmp/a', id='file-tmp-a'),
        pytest.param('http://user:secret@printer', id='http-user-secret-printer'),
        pytest.param('http://printer/path', id='http-printer-path'),
        pytest.param('http://printer?token=x', id='http-printer-token-x'),
        pytest.param('http://printer:99999', id='http-printer-99999'),
    ])
    def test_address_validation(self, value):
        assert plugin.printer_url('printer.local:7125') == 'http://printer.local:7125'
        with pytest.raises(ValueError):
            plugin.printer_url(value)

    def test_snapshot_marks_old_timestamp_stale(self, http_endpoint):
        printer = plugin.Printer(http_endpoint, "secret")
        frame = printer.snapshot()
        assert frame['frozen']
        assert frame['image'].startswith('data:image/jpeg;base64,')
        first_request = Endpoint.requests[-1][0]
        printer.snapshot()
        assert Endpoint.requests[-1][0] != first_request, 'Repeated snapshots must bypass the HTTP cache'
        assert Endpoint.requests[-1][1] == 'secret'

    def test_fps_counts_new_images_instead_of_downloads(self, http_endpoint):
        printer = plugin.Printer(http_endpoint)
        printer.http.camera_snapshot = Mock(side_effect=[plugin.CameraSnapshot(b'\xff\xd8first', 1_000_000_000),
                                        plugin.CameraSnapshot(b'\xff\xd8first', 1_000_000_000),
                                        plugin.CameraSnapshot(b'\xff\xd8second', 1_000_000_000)])
        with patch.object(plugin.time, "monotonic", side_effect=[10, 10.1, 10.2]), \
                patch.object(plugin.time, "time", return_value=1_000_000_000):
            displayed_rates = [printer.snapshot()["fps"]
                               for _ in range(3)]
            assert displayed_rates == [0, 0, 5], 'Downloading an identical image must not increase the displayed frame rate'

    def test_stream_freshness_uses_timestamp_instead_of_identical_image_content(self, http_endpoint):
        printer = plugin.Printer(http_endpoint)
        printer.http.camera_snapshot = Mock(return_value=plugin.CameraSnapshot(b'\xff\xd8still', 100))
        with patch.object(plugin.time, "time", return_value=105):
            assert not printer.snapshot()['frozen']
        with patch.object(plugin.time, "time", return_value=106):
            assert printer.snapshot()['frozen']
        printer.http.camera_snapshot.return_value = plugin.CameraSnapshot(b'\xff\xd8still', None)
        assert not printer.snapshot()['frozen']

    def test_http_does_not_follow_redirect_or_accept_oversized_data(self, http_endpoint):
        client = plugin.HttpClient(http_endpoint)
        with pytest.raises(RuntimeError, match='302'):
            client.request("/redirect")
        with pytest.raises(RuntimeError, match='too large'):
            client.request("/large", limit=5)

    def test_http_json_posts_payload_with_api_key(self, http_endpoint):
        client = plugin.HttpClient(http_endpoint, "secret")
        assert client.json('/echo', {'enabled': True}) == {'result': {'enabled': True}}
        assert Endpoint.requests[-1] == ('/echo', 'secret', 'application/json')

    @pytest.mark.parametrize(
        "code,api_key",
        [
            pytest.param(401, None, id="unauthorized-no-key"),
            pytest.param(401, "secret", id="unauthorized-configured-key"),
            pytest.param(403, None, id="forbidden-no-key"),
            pytest.param(403, "secret", id="forbidden-configured-key"),
        ],
    )
    def test_http_denied_access_has_a_clear_error(self, http_endpoint, code, api_key):
        with patch.object(Endpoint, "status_code", code), \
                patch.object(Endpoint, "status_body", b"private-response-secret"):
            with pytest.raises(plugin.AccessDenied, match="denied access") as raised:
                plugin.HttpClient(http_endpoint, api_key).request("/printer/objects/query?")
            message = str(raised.value)
            assert "private-response-secret" not in message
            if api_key:
                assert api_key not in message
            assert Endpoint.requests[-1][1] == api_key


    def test_keepalive_and_oneshot_authentication(self, http_endpoint):
        ws = Mock()
        ws.recv.side_effect = [json.dumps({"method": "notification"}), json.dumps({"id": 1, "result": {}})]
        with patch.object(plugin.websocket, "create_connection", return_value=ws) as connect:
            plugin.Printer(http_endpoint, "api-key").start_monitor()
        assert json.loads(ws.send.call_args.args[0]) == {'jsonrpc': '2.0', 'id': 1, 'method': 'camera.start_monitor', 'params': {'domain': 'lan', 'interval': 0}}
        assert connect.call_args.args[0].endswith('/websocket?token=token')
        assert connect.call_args.kwargs['http_no_proxy'] == ['*']
        assert connect.call_args.kwargs['redirect_limit'] == 0
        assert connect.call_args.kwargs['class_'] is plugin.CameraWebSocket

    @pytest.mark.parametrize('failure', [
        pytest.param(RuntimeError('rejected'), id='rejected-activation'),
        pytest.param(OSError('disconnected'), id='disconnected-socket'),
    ])
    def test_camera_failure_replaces_socket_and_preserves_status(self, http_endpoint, request, failure):
        printer = plugin.Printer(http_endpoint)
        request.addfinalizer(printer.close)
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
            with pytest.raises(type(failure)):
                printer.start_monitor()
            assert printer.http.ws is None
            failed.close.assert_called_once_with(timeout=0)
            assert printer.status()['printer'] == 'standby'
            printer.start_monitor()
        assert connect.call_count == 2
        assert printer.http.ws is recovered

    def test_monitor_reuses_connection_matches_ids_and_closes_once(self, http_endpoint):
        printer = plugin.Printer(http_endpoint)
        ws = Mock()
        ws.recv.side_effect = [json.dumps({"id": 1, "result": {}}),
                               json.dumps({"id": 1, "result": {}}),
                               json.dumps({"id": 2, "result": {}})]
        with patch.object(plugin.websocket, "create_connection", return_value=ws) as connect:
            printer.start_monitor()
            printer.start_monitor()
        connect.assert_called_once()
        assert [json.loads(call.args[0])['id'] for call in ws.send.call_args_list] == [1, 2]
        assert ws.recv.call_count == 3
        printer.close()
        printer.close()
        ws.close.assert_called_once_with(timeout=0)

    def test_monitor_renewal_resets_send_timeout_after_late_ack(self, http_endpoint):
        client = plugin.HttpClient(http_endpoint)
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
        assert all((1 < timeout <= 2 for timeout in send_timeouts))
        client.close()

    def test_monitor_renewal_reads_ack_after_queued_notifications(self, http_endpoint):
        client = plugin.HttpClient(http_endpoint)
        ws = Mock()
        ws.recv.side_effect = [json.dumps({"id": 1, "result": {}})] + [
            json.dumps({"method": "notify_status_update", "params": [{}, 1]})
        ] * 100 + [json.dumps({"id": 2, "result": {}})]
        with patch.object(plugin.websocket, "create_connection", return_value=ws), \
                patch.object(plugin, "NETWORK_TIMEOUT", 2):
            client.start_monitor()
            client.start_monitor()
        assert ws.recv.call_count == 102
        client.close()

    def test_monitor_limits_responses(self, http_endpoint):
        client = plugin.HttpClient(http_endpoint)
        ws = Mock()
        ws.recv.return_value = 'x' * 512_001
        with patch.object(plugin.websocket, "create_connection", return_value=ws), \
                pytest.raises(RuntimeError, match='too large'):
            client.start_monitor()
        client.close()

    def test_snapshot_validates_jpeg_and_decodes_optional_timestamp(self, http_endpoint):
        client = plugin.HttpClient(http_endpoint)
        with patch.object(client, "request", return_value=(b'not a jpeg', None)), \
                pytest.raises(RuntimeError, match='JPEG'):
            client.camera_snapshot()
        with patch.object(client, "request", return_value=(b'\xff\xd8image', 'invalid')), \
                pytest.raises(RuntimeError, match='timestamp'):
            client.camera_snapshot()
        with patch.object(client, "request", return_value=(b'\xff\xd8image', None)):
            assert client.camera_snapshot() == plugin.CameraSnapshot(b'\xff\xd8image', None)

    def test_light_only_sends_fixed_led_command(self, http_endpoint):
        printer = plugin.Printer(http_endpoint)
        printer.set_light(True)
        assert Endpoint.requests[-1][0] == '/printer/gcode/script'
        assert Endpoint.payloads[-1] == {'script': 'SET_LED LED=cavity_led WHITE=1 SYNC=0'}
        printer.set_light(False)
        assert Endpoint.payloads[-1] == {'script': 'SET_LED LED=cavity_led WHITE=0 SYNC=0'}
        request_count = len(Endpoint.requests)
        with pytest.raises(ValueError):
            printer.set_light("M112")
        assert len(Endpoint.requests) == request_count

    def test_object_queries_encode_names_and_fields_and_unwrap_status(self, http_endpoint):
        client = plugin.HttpClient(http_endpoint, "secret")
        status = client.query_objects(plugin.ObjectQuery("print_stats", ("state", "filename")),
                                      plugin.ObjectQuery("led cavity_led&other"))
        assert status['print_stats']['state'] == 'standby'
        assert Endpoint.requests[-1] == ('/printer/objects/query?print_stats=state,filename&led%20cavity_led%26other', 'secret')
        result = plugin.Printer(http_endpoint).status()
        assert result['printer'] == 'standby'
        assert result['light']
        assert result['stats']['progress'] is None


    @pytest.mark.parametrize('layer,phase', [
        pytest.param(0, 'preparing', id='preparing-layer-zero'),
        pytest.param(None, 'unknown', id='missing-layer'),
        pytest.param(0.5, 'unknown', id='fractional-layer'),
        pytest.param(True, 'unknown', id='boolean-layer'),
        pytest.param(-1, 'unknown', id='negative-layer'),
        pytest.param(101, 'unknown', id='above-total-layer'),
    ])
    def test_orca_time_plan_uses_confirmed_progress_and_model_phase(self, layer, phase):
        metadata = {"slicer": "OrcaSlicer", "estimated_time": 2081}
        result = plugin.print_metrics(estimate_status(), metadata)
        assert result['remaining'] == pytest.approx(1331.84, abs=0.5 * 10 ** (-7), rel=0)
        assert result['planned_total'] == 2081
        assert result['phase'] == 'printing'
        status = estimate_status()
        status["print_stats"]["info"]["current_layer"] = layer
        result = plugin.print_metrics(status, metadata)
        assert result['phase'] == phase
        assert result['remaining'] is None
        assert result['planned_total'] == 2081

    @pytest.mark.parametrize("section,field,value",
        [("display_status", "progress", value) for value in (None, True, -1, 1.1, float("nan"), float("inf"), .365)] +
        [("virtual_sdcard", "progress", value) for value in (None, .36, True, -1, 2)] +
        [("gcode_move", "speed_factor", value) for value in (None, True, .5, 2)],
        ids=["display-missing", "display-boolean", "display-negative", "display-excessive", "display-nan", "display-infinite", "display-fractional-m73",
             "bytes-missing", "bytes-equals-display", "bytes-boolean", "bytes-negative", "bytes-excessive",
             "speed-missing", "speed-boolean", "speed-slow", "speed-fast"])
    def test_estimation_rejects_ambiguous_or_invalid_sources(self, section, field, value):
        status = estimate_status()
        status[section][field] = value
        metadata = {"slicer": "OrcaSlicer", "estimated_time": 2081}
        assert plugin.print_metrics(status, metadata)["remaining"] is None

    @pytest.mark.parametrize('invalid', [
        pytest.param(None, id='missing'),
        pytest.param([], id='wrong-shape'),
        pytest.param({'estimated_time': 2081}, id='missing-slicer'),
        pytest.param({'slicer': 'SnapmakerOrca', 'estimated_time': 2081}, id='unsupported-slicer'),
    ])
    def test_estimation_requires_matching_slicer_metadata(self, invalid):
        assert plugin.print_metrics(estimate_status(), invalid)["remaining"] is None

    @pytest.mark.parametrize('value', [
        pytest.param(None, id='missing'),
        pytest.param(True, id='boolean'),
        pytest.param(0, id='zero'),
        pytest.param(-1, id='negative'),
        pytest.param('2081', id='string'),
        pytest.param(float('nan'), id='nan'),
        pytest.param(float('inf'), id='infinite'),
    ])
    def test_estimation_rejects_invalid_planned_times(self, value):
        metadata = {"slicer": "OrcaSlicer", "estimated_time": value}
        result = plugin.print_metrics(estimate_status(), metadata)
        assert result["remaining"] is None
        assert result["planned_total"] is None

    @pytest.mark.parametrize('display,byte_progress', [
        pytest.param(0.360000001, 0.3, id='rounded-m73'),
        pytest.param(0.36, 0.3600005, id='byte-progress-fallback'),
    ])
    def test_estimation_uses_tolerant_m73_detection(self, display, byte_progress):
        status = estimate_status()
        status["display_status"]["progress"] = display
        status["virtual_sdcard"]["progress"] = byte_progress
        metadata = {"slicer": "OrcaSlicer", "estimated_time": 2081}
        remaining = plugin.print_metrics(status, metadata)["remaining"]
        assert (remaining is not None) == (byte_progress == .3)

    def test_pause_resume_finishing_and_terminal_estimates(self):
        status = estimate_status()
        metadata = {"slicer": "OrcaSlicer", "estimated_time": 2081}
        for state in ("paused", "cancelled", "error", "standby"):
            status["print_stats"]["state"] = state
            assert plugin.print_metrics(status, metadata)['remaining'] is None
        status["print_stats"]["state"] = "printing"
        assert plugin.print_metrics(status, metadata)['remaining'] == pytest.approx(1331.84, abs=0.5 * 10 ** (-7), rel=0)
        status["display_status"]["progress"] = .30966
        assert plugin.print_metrics(status, metadata)['remaining'] is None
        status["display_status"]["progress"] = 1
        result = plugin.print_metrics(status, metadata)
        assert result['phase'] == 'finishing'
        assert result['remaining'] is None
        status["print_stats"]["state"] = "complete"
        result = plugin.print_metrics(status, metadata)
        assert (result['remaining'], result['progress']) == (0, 1)

    def test_metadata_cache_tracks_run_resets_and_refreshes_with_bounded_retry(self):
        printer = plugin.Printer("http://printer.local")
        status = estimate_status()
        printer.http.query_objects = Mock(return_value=status)
        printer.http.file_metadata = Mock(return_value={"slicer": "OrcaSlicer", "estimated_time": 2081,
                                                        "modified": 1})
        with patch.object(plugin.time, "monotonic", return_value=100) as clock:
            assert printer.status()['stats']['planned_total'] is None
            printer.refresh_metadata()
            assert printer.status()['stats']['remaining'] == pytest.approx(1331.84, abs=0.5 * 10 ** (-7), rel=0)
            queries = printer.http.query_objects.call_args.args
            for name in ("fan_generic cavity_fan", "fan", "fan_generic e1_fan", "fan_generic e2_fan", "fan_generic e3_fan", "heater_fan power_fan", *("heater_fan e%d_nozzle_fan" % tool for tool in range(4))):
                assert plugin.ObjectQuery(name, ('speed', 'rpm')) in queries
            assert plugin.ObjectQuery('purifier', ('power_detected', 'exhaust_fan', 'inner_fan', 'inner_fan_rpm')) in queries
            assert plugin.ObjectQuery('gcode_move', ('speed_factor',)) in queries
            assert plugin.ObjectQuery('virtual_sdcard', ('progress', 'file_position', 'file_size')) in queries
            for state in ("paused", "printing"):
                status["print_stats"]["state"] = state
                assert printer.status()['stats']['planned_total'] == 2081
                printer.refresh_metadata()
            assert printer.http.file_metadata.call_count == 1
            clock.return_value = 130
            printer.http.file_metadata.return_value = {"slicer": "OrcaSlicer", "estimated_time": 3000,
                                                        "modified": 2}
            assert printer.status()['stats']['planned_total'] is None
            printer.refresh_metadata()
            assert printer.status()['stats']['planned_total'] == 3000
            printer.http.file_metadata.side_effect = RuntimeError("Unavailable")
            clock.return_value = 160
            assert printer.status()['stats']['planned_total'] is None
            printer.refresh_metadata()
            assert printer.status()['stats']['planned_total'] is None
            before = printer.http.file_metadata.call_count
            printer.status()
            printer.refresh_metadata()
            assert printer.http.file_metadata.call_count == before
            for section, field, value in [("print_stats", "filename", "second.gcode"),
                                          ("print_stats", "total_duration", 1),
                                          ("virtual_sdcard", "file_position", 1),
                                          ("virtual_sdcard", "file_size", 2000)]:
                before = printer.http.file_metadata.call_count
                status[section][field] = value
                assert printer.status()['stats']['planned_total'] is None
                printer.refresh_metadata()
                assert printer.http.file_metadata.call_count == before + 1
                printer.status()
                printer.refresh_metadata()
                assert printer.http.file_metadata.call_count == before + 1
            # Same-name reprint after completion fetches immediately, even after failure.
            status["print_stats"]["state"] = "complete"
            printer.status()
            status["print_stats"]["state"] = "printing"
            before = printer.http.file_metadata.call_count
            printer.status()
            printer.refresh_metadata()
            assert printer.http.file_metadata.call_count == before + 1
            clock.return_value = 190
            printer.http.file_metadata.side_effect = None
            printer.http.file_metadata.return_value = []
            printer.status()
            printer.refresh_metadata()
            assert printer.status()['stats']['planned_total'] is None
            before = printer.http.file_metadata.call_count
            printer.status()
            printer.refresh_metadata()
            assert printer.http.file_metadata.call_count == before
            clock.return_value = 220
            printer.http.file_metadata.return_value = {"slicer": "OrcaSlicer", "estimated_time": 2081}
            printer.status()
            printer.refresh_metadata()
            assert printer.status()['stats']['planned_total'] == 2081

    def test_metadata_size_must_match_the_active_reader_when_available(self):
        printer = plugin.Printer("http://printer.local")
        printer.http.query_objects = Mock(return_value=estimate_status())
        printer.http.file_metadata = Mock(return_value={"slicer": "OrcaSlicer", "estimated_time": 2081,
                                                        "size": 2000})
        printer.status()
        printer.refresh_metadata()
        assert printer.status()['stats']['planned_total'] is None
        printer.metadata_retry_at = 0
        printer.http.file_metadata.return_value["size"] = 1000
        printer.status()
        printer.refresh_metadata()
        assert printer.status()['stats']['remaining'] == pytest.approx(1331.84, abs=0.5 * 10 ** (-7), rel=0)

    @pytest.mark.parametrize('section,field,value', [
        pytest.param('print_stats', 'filename', 'next.gcode', id='filename'),
        pytest.param('print_stats', 'total_duration', 1, id='total_duration'),
        pytest.param('virtual_sdcard', 'file_position', 1, id='file_position'),
        pytest.param('virtual_sdcard', 'file_size', 2000, id='file_size'),
        pytest.param('print_stats', 'state', 'complete', id='state'),
    ])
    def test_cached_metadata_is_revalidated_before_enriching_a_fresh_run(self, section, field, value):
        changes = [("print_stats", "filename", "next.gcode"),
                   ("print_stats", "total_duration", 1),
                   ("virtual_sdcard", "file_position", 1),
                   ("virtual_sdcard", "file_size", 2000),
                   ("print_stats", "state", "complete")]
        printer = plugin.Printer("http://printer")
        status = estimate_status()
        printer.http.query_objects = Mock(return_value=status)
        def metadata(filename):
            status[section][field] = value
            return {"estimated_time": 2081, "size": 1000}

        printer.http.file_metadata = Mock(side_effect=metadata)
        assert printer.status()['stats']['planned_total'] is None
        printer.refresh_metadata()
        assert printer.status()['stats']['planned_total'] is None
        if field == "state":
            status[section][field] = "printing"
            assert printer.status()['stats']['planned_total'] is None
        assert printer.http.file_metadata.call_count == 1

    def test_file_metadata_encodes_filename_and_uses_authenticated_transport(self):
        client = plugin.HttpClient("http://printer.local", "secret")
        with patch.object(client, "json", return_value={"result": {"estimated_time": 2081}}) as request:
            assert client.file_metadata('folder/a & b.gcode') == {'estimated_time': 2081}
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
        assert (result['tool'], result['material'], result['color']) == (2, 'PETG', '#12AB34')
        assert (result['elapsed'], result['remaining']) == (4200, None)
        assert result['hotend']['temperature'] == 235
        assert result['current_layer'] == 100
        status["display_status"]["progress"] = 0
        assert plugin.print_metrics(status)['remaining'] is None
        status["print_stats"]["state"] = "complete"
        assert plugin.print_metrics(status)['progress'] == 1
        assert plugin.print_metrics(status)['remaining'] == 0

    def test_fans_follow_connected_tool_and_reject_missing_or_invalid_readings(self):
        status = {"fan_generic cavity_fan": {"speed": .7, "rpm": 3200}}
        for tool, name in enumerate(("fan", "fan_generic e1_fan", "fan_generic e2_fan", "fan_generic e3_fan")):
            head = "extruder" + (str(tool) if tool else "")
            status.update(toolhead={"extruder": head})
            status[head] = {"state": "ACTIVATE"}
            status[name] = {"speed": .5, "rpm": 8100}
            status["heater_fan e%d_nozzle_fan" % tool] = {"speed": .75, "rpm": 16761}
            result = plugin.print_metrics(status)
            assert result['aux_fan'] == {'speed': 0.7, 'rpm': 3200}
            assert result['part_fan'] == {'speed': 0.5, 'rpm': 8100}
            assert result['hotend_fan'] == {'speed': 0.75, 'rpm': 16761}
            status[head]["state"] = "PARKED"
        assert plugin.print_metrics(status)['part_fan'] == {'speed': None, 'rpm': None}
        for speed, rpm in ((None, None), (True, False), (-1, -1), (1.1, float("nan"))):
            status["fan_generic cavity_fan"] = {"speed": speed, "rpm": rpm}
            assert plugin.print_metrics(status)['aux_fan'] == {'speed': None, 'rpm': None}
        status["fan_generic cavity_fan"] = {"speed": 0, "rpm": 0}
        assert plugin.print_metrics(status)['aux_fan'] == {'speed': 0, 'rpm': 0}

    def test_additional_fans_require_current_valid_status_and_connected_cover(self):
        status = {"heater_fan power_fan": {"speed": .6, "rpm": None},
                  "purifier": {"power_detected": True, "exhaust_fan": {"speed": .6},
                               "inner_fan": {"speed": 0}, "inner_fan_rpm": 0}}
        result = plugin.print_metrics(status)
        assert result['psu_fan'] == {'speed': 0.6, 'rpm': None}
        assert result['exhaust_fan'] == {'speed': 0.6, 'rpm': None}
        assert result['circulation_fan'] == {'speed': 0, 'rpm': 0}
        assert result['hotend_fan'] == {'speed': None, 'rpm': None}
        for power in (False, None, 1):
            status["purifier"]["power_detected"] = power
            result = plugin.print_metrics(status)
            for name in ("exhaust_fan", "circulation_fan"):
                assert result[name] == {'speed': None, 'rpm': None}
        assert plugin.print_metrics({})['psu_fan'] == {'speed': None, 'rpm': None}

    def test_four_toolheads_use_physical_status_and_live_nozzle_sizes(self):
        status = {"toolhead": {"extruder": "extruder"},
                  "extruder": {"state": "PARKED", "temperature": 25, "target": 0, "nozzle_diameter": .4},
                  "extruder1": {"state": "ACTIVATE", "temperature": 180, "target": 240, "nozzle_diameter": .6},
                  "extruder2": {"state": "UNKNOWN", "nozzle_diameter": float("nan")},
                  "print_task_config": {"filament_type": ["PLA", "PETG", "NONE"],
                                        "filament_color_rgba": ["FFFFFFFF", "invalid"],
                                        "filament_exist": [True, False], "extruder_map_table": [1]}}
        result = plugin.print_metrics(status, {"slicer": "OrcaSlicer", "filament_name": "PETG profile", "layer_height": .2})
        assert [tool['state'] for tool in result['tools']] == ['parked', 'connected', 'unknown', 'unavailable']
        assert result['tools'][1]['nozzle_diameter'] == 0.6
        assert result['tools'][1]['heater'] == {'temperature': 180, 'target': 240}
        assert result['tools'][1]['color'] is None
        assert not result['tools'][1]['filament_present']
        assert result['tools'][1]['material_profile'] == 'PETG profile'
        assert result['tools'][2]['material'] is None
        assert result['tools'][2]['nozzle_diameter'] is None
        assert result['layer_height'] == 0.2
        status["extruder1"]["activating_move"] = True
        assert plugin.print_metrics(status)['tools'][1]['state'] == 'changing'
        empty = plugin.print_metrics({})
        assert len(empty['tools']) == 4
        assert all((tool['state'] == 'unavailable' for tool in empty['tools']))
        assert plugin.print_metrics({}, {'layer_height': -1})['layer_height'] is None

    def test_filament_details_follow_physical_tool_and_sliced_mapping(self):
        status = {"toolhead": {"extruder": "extruder2"},
                  "print_task_config": {"filament_type": ["PLA", "PLA", "PETG"],
                                        "filament_vendor": ["Snapmaker", "Generic", " Polymaker "],
                                        "filament_sub_type": ["SnapSpeed", "Basic", " HF "],
                                        "extruder_map_table": [2, 0]}}
        metadata = {"slicer": "OrcaSlicer", "filament_name": 'PETG profile";"PLA profile',
                    "filament_used_mm": [100, 200]}
        result = plugin.print_metrics(status, metadata)
        assert (result['material_vendor'], result['material_subtype'], result['material_profile']) == ('Polymaker', 'HF', 'PETG profile')
        status["toolhead"]["extruder"] = "extruder"
        assert plugin.print_metrics(status, metadata)['material_profile'] == 'PLA profile'
        status["print_task_config"]["extruder_map_table"] = [0, 2]
        assert plugin.print_metrics(status, metadata)['material_profile'] == 'PETG profile'
        status["toolhead"]["extruder"] = "unknown"
        result = plugin.print_metrics(status, metadata)
        for key in ("material", "material_vendor", "material_subtype", "material_profile"):
            assert result[key] is None

    @pytest.mark.parametrize("raw,used,expected", [
        pytest.param('PLA profile;PETG profile', [100, 200], None, id="ambiguous-profiles"),
        pytest.param('PLA profile;PLA profile', [100, 200], 'PLA profile', id="same-profile"),
        pytest.param('PLA profile;Unused profile', [100, 0], 'PLA profile', id="unused-profile"),
        pytest.param('PLA profile;Unused profile', [100, 200, 300], None, id="inconsistent-usage"),
        pytest.param('Custom; PLA', [100], 'Custom; PLA', id="semicolon-in-name"),
        pytest.param('Custom; PLA";"PETG profile', [100, 200], None, id="ambiguous-semicolon"),
        pytest.param('"PLA profile";"PLA profile"', [100, 200], 'PLA profile', id="quoted-profiles"),
        pytest.param('NONE', [100], None, id="none-marker"),
        pytest.param('', [100], None, id="empty"),
    ])
    def test_sliced_profiles_handle_quotes_and_hide_ambiguous_or_missing_details(self, raw, used, expected):
        status = {"toolhead": {"extruder": "extruder"},
                  "print_task_config": {"filament_type": ["PLA"], "filament_vendor": ["NONE"],
                                        "filament_sub_type": [42], "extruder_map_table": [0, 0]}}
        metadata = {"slicer": "SnapmakerOrca", "filament_name": raw, "filament_used_mm": used}
        result = plugin.print_metrics(status, metadata)
        assert result["material_profile"] == expected
        assert result["material_vendor"] is None
        assert result["material_subtype"] is None

    @pytest.mark.parametrize('mapping', [
        pytest.param(None, id='missing'),
        pytest.param([], id='empty'),
        pytest.param([True], id='boolean'),
        pytest.param(['0'], id='string'),
        pytest.param([1], id='wrong-tool'),
    ])
    def test_sliced_profiles_require_valid_tool_mapping(self, mapping):
        status = {"toolhead": {"extruder": "extruder"},
                  "print_task_config": {"filament_type": ["PLA"], "filament_vendor": ["NONE"],
                                        "filament_sub_type": [42], "extruder_map_table": mapping}}
        metadata = {"slicer": "OrcaSlicer", "filament_name": "PLA profile", "filament_used_mm": [100]}
        assert plugin.print_metrics(status, metadata)["material_profile"] is None

    def test_filament_profile_metadata_does_not_require_a_time_estimate(self):
        printer = plugin.Printer("http://printer.local")
        status = estimate_status()
        status.update({"toolhead": {"extruder": "extruder"},
                       "print_task_config": {"extruder_map_table": [0]}})
        printer.http.query_objects = Mock(return_value=status)
        printer.http.file_metadata = Mock(return_value={"slicer": "OrcaSlicer", "filament_name": "PLA profile"})
        assert printer.status()['stats']['material_profile'] is None
        printer.refresh_metadata()
        result = printer.status()["stats"]
        assert result['material_profile'] == 'PLA profile'
        assert result['planned_total'] is None
        fields = next(query.fields for query in printer.http.query_objects.call_args.args
                      if query.name == "print_task_config")
        assert {'filament_vendor', 'filament_sub_type', 'extruder_map_table'}.issubset(fields)
        printer.http.file_metadata.return_value["size"] = 2000
        status["virtual_sdcard"]["file_size"] = 1000
        printer.metadata_retry_at = 0
        printer.status()
        printer.refresh_metadata()
        assert printer.status()['stats']['material_profile'] is None

    def test_print_metrics_missing_or_invalid_values_remain_unknown(self):
        result = plugin.print_metrics({"display_status": {"progress": float("nan")},
                                       "virtual_sdcard": {"progress": .25},
                                       "toolhead": {"extruder": "unknown"},
                                       "print_stats": {"total_duration": -1}})
        assert result['progress'] == 0.25
        assert result['elapsed'] is None
        assert result['tool'] is None
        assert result['hotend'] == {}

    def test_client_rejects_api_errors_and_escapes_websocket_tokens(self):
        client = plugin.HttpClient("https://printer.local:7125", "secret")
        with patch.object(client, "json", return_value={"error": {"message": "secret"}}):
            with pytest.raises(RuntimeError, match='rejected') as raised:
                client.query_objects(plugin.ObjectQuery("print_stats"))
        assert 'secret' not in str(raised.value)
        with patch.object(client, "json", return_value={"result": "a&b?c/#"}):
            assert client.websocket_url() == 'wss://printer.local:7125/websocket?token=a%26b%3Fc%2F%23'
        assert plugin.HttpClient('http://printer.local').websocket_url() == 'ws://printer.local/websocket'


def eventually(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate(), 'Worker did not reach expected state'


class TestWorkerMetadata:
    @pytest.mark.parametrize('failure', [
        pytest.param(False, id='metadata-available'),
        pytest.param(True, id='metadata-failure'),
    ])
    def test_metadata_enriches_only_the_next_fresh_status(self, failure):
        now, messages, queries = [100.0], [], []
        printer = plugin.Printer("http://printer")

        def query(*objects):
            queries.append(now[0])
            status = estimate_status()
            status["heater_bed"] = {"temperature": 60 + len(queries)}
            return status

        def publish(message):
            if "stats" in message:
                messages.append(message)
                if len(messages) == 2:
                    monitor.closed = True

        def wait(timeout):
            now[0] += .25
            monitor.update(("printer", ""), playing=False)

        printer.http.query_objects = Mock(side_effect=query)
        printer.http.file_metadata = Mock(return_value={"estimated_time": 2081})
        if failure:
            printer.http.file_metadata.side_effect = OSError("http://printer?token=secret")
        with patch.object(plugin.threading.Thread, "start"), \
                patch.object(plugin.time, "monotonic", side_effect=lambda: now[0]):
            monitor = plugin.Monitor(publish, lambda *target: printer)
            monitor.condition.wait = wait
            monitor.update(("printer", ""), playing=False)
            monitor.run()
        assert queries == [100, 103]
        assert [m['stats']['bed']['temperature'] for m in messages] == [61, 62]
        assert [m['stats']['planned_total'] for m in messages] == [None, None if failure else 2081]
        assert 'secret' not in str(messages)
        assert all(('status_error' not in m for m in messages))
        printer.http.file_metadata.assert_called_once_with("part.gcode")

    @pytest.mark.parametrize('unload', [
        pytest.param(False, id='target-change'),
        pytest.param(True, id='unload'),
    ])
    def test_target_change_and_unload_cancel_blocked_metadata(self, unload):
        local, peer = socket.socketpair()
        entered, next_status = threading.Event(), threading.Event()
        messages = []
        printer = plugin.Printer("http://first")
        printer.http.query_objects = Mock(return_value=estimate_status())
        printer.http.file_metadata = lambda filename: printer.http.json("/metadata")
        second = Mock()

        def status():
            next_status.set()
            return {"printer": "standby", "stats": {"planned_total": None}}

        second.status.side_effect = status
        clients = iter((printer, second))
        monitor = plugin.Monitor(messages.append, lambda *target: next(clients))

        def connect(operation, *args):
            entered.set()
            return operation.track(local)

        try:
            with patch.object(plugin.NetworkOperation, "connect", connect):
                monitor.update(("first", ""), playing=False)
                assert entered.wait(1)
                assert any(('stats' in m for m in messages))
                started = time.monotonic()
                if unload:
                    monitor.close()
                    assert not monitor.thread.is_alive()
                else:
                    monitor.update(("second", ""), playing=False)
                    assert next_status.wait(0.5)
                assert time.monotonic() - started < 0.7
                assert printer.http.closed
                assert printer.metadata is None
                assert not any((m.get('failed') for m in messages))
        finally:
            monitor.close()
            peer.close()

    def test_late_metadata_cannot_enrich_a_changed_target(self):
        entered, release, published = threading.Event(), threading.Event(), threading.Event()
        first, second = plugin.Printer("http://first"), plugin.Printer("http://second")
        for printer in (first, second):
            printer.http.query_objects = Mock(return_value=estimate_status())
        second.http.file_metadata = Mock(return_value={})

        def metadata(filename):
            entered.set()
            release.wait()
            return {"estimated_time": 2081}

        first.http.file_metadata = Mock(side_effect=metadata)
        messages = []

        def publish(message):
            messages.append(message)
            if message.get("generation") == 2 and "stats" in message:
                published.set()

        clients = iter((first, second))
        monitor = plugin.Monitor(publish, lambda *target: next(clients))
        try:
            monitor.update(("first", ""), playing=False)
            assert entered.wait(1)
            monitor.update(("second", ""), playing=False)
            release.set()
            assert published.wait(1)
            live = [m for m in messages if "stats" in m]
            assert [m['generation'] for m in live] == [1, 2]
            assert all((m['stats']['planned_total'] is None for m in live))
        finally:
            release.set()
            monitor.close()

    @pytest.mark.parametrize('failure', [
        pytest.param(False, id='metadata-available'),
        pytest.param(True, id='metadata-failure'),
    ])
    def test_live_status_is_published_before_blocked_metadata(self, failure):
        entered, release = threading.Event(), threading.Event()
        messages = []
        printer = plugin.Printer("http://printer")
        status = estimate_status()
        status.update({"heater_bed": {"temperature": 60},
                       "fan_generic cavity_fan": {"speed": .5},
                       "led cavity_led": {"color_data": [[0, 0, 0, 1]]}})
        printer.http.query_objects = Mock(return_value=status)

        def metadata(filename):
            entered.set()
            release.wait()
            if failure:
                raise OSError("metadata unavailable")
            return {"estimated_time": 2081, "slicer": "OrcaSlicer"}

        printer.http.file_metadata = Mock(side_effect=metadata)
        monitor = plugin.Monitor(messages.append, lambda *target: printer)
        try:
            monitor.update(("printer", ""), playing=False)
            assert entered.wait(1)
            live = [message for message in messages if "stats" in message]
            assert len(live) == 1, 'Live status must arrive while metadata is still blocked'
            assert live[0]['stats']['bed']['temperature'] == 60
            assert live[0]['stats']['progress'] == 0.36
            assert live[0]['stats']['current_layer'] == 30
            assert live[0]['stats']['aux_fan']['speed'] == 0.5
            assert live[0]['light']
            assert live[0]['stats']['planned_total'] is None
        finally:
            release.set()
            monitor.close()


class TestWorkerSchedule:
    def test_idle_worker_waits_for_a_local_recheck(self, scheduled_worker):
        state = scheduled_worker
        state.monitor.run()
        assert state.waits == [300]
        state.factory.assert_not_called()

    def test_idle_timeouts_recheck_without_work_and_update_wakes_immediately(self, scheduled_worker):
        state = scheduled_worker
        created_at = []
        def create(*target):
            created_at.append(state.now)
            return state.client
        state.factory.side_effect = create
        def wait(timeout):
            state.waits.append(timeout)
            if len(state.waits) == 1:
                state.now += timeout
            elif len(state.waits) == 2:
                state.now += .1
                state.monitor.update(("printer", ""), playing=False)
            else:
                state.monitor.closed = True
        state.monitor.condition.wait = wait
        state.monitor.run()
        assert state.waits == [300, 300, 3]
        assert created_at == [400.1]
        state.factory.assert_called_once_with("printer", "")
        state.client.status.assert_called_once()

    @pytest.mark.parametrize("duration,command_duration,expected", [
        pytest.param(0, 0, 3, id="next-status"),
        pytest.param(1, 0, 2, id="status-after-work"),
        pytest.param(0, 1, 2.5, id="lease-before-forced-readback"),
    ])
    def test_stopped_camera_waits_for_status_or_lease(self, scheduled_worker, duration, command_duration, expected):
        state = scheduled_worker
        def status():
            state.now += duration
            return {"printer": "standby"}
        state.client.status.side_effect = status
        def command(enabled):
            state.now += command_duration
        state.client.set_light.side_effect = command
        state.monitor.update(("printer", ""), True if command_duration else None, playing=False)
        state.monitor.run()
        assert state.waits == [expected]
        state.client.start_monitor.assert_not_called()
        state.client.snapshot.assert_not_called()
        state.client.close.assert_called_once()

    def test_factory_retry_expires_into_a_bounded_idle_wait(self, scheduled_worker):
        state = scheduled_worker
        attempts = []
        def fail(*target):
            attempts.append(state.now)
            raise OSError("unavailable")
        state.factory.side_effect = fail
        def wait(timeout=None):
            state.waits.append(timeout)
            if len(state.waits) >= 3:
                state.monitor.closed = True
            else:
                state.now += timeout
        state.monitor.condition.wait = wait
        state.monitor.update(("printer", ""), playing=False)
        state.monitor.run()
        assert state.waits == [3, .5, 300]
        assert attempts == [100, 103]
        assert sum(message.get("failed", False) for message in state.messages) == 2

    def test_generation_change_does_not_inherit_a_failed_factory_retry(self, scheduled_worker):
        state = scheduled_worker
        attempts = []
        def create(*target):
            attempts.append((target, state.now))
            if len(attempts) == 1:
                raise OSError("unavailable")
            return state.client
        state.factory.side_effect = create
        def wait(timeout):
            state.waits.append(timeout)
            if len(state.waits) == 1:
                state.now += .25
                state.monitor.update(("second", ""), playing=False)
            else:
                state.monitor.closed = True
        state.monitor.condition.wait = wait
        state.monitor.update(("first", ""), playing=False)
        state.monitor.run()
        assert attempts == [(("first", ""), 100), (("second", ""), 100.25)]
        assert state.waits == [3, 3]
        assert not any(message.get("generation") == 1 and "printer" in message
                       for message in state.messages)

    def test_camera_retry_keeps_independent_status_deadlines(self, scheduled_worker):
        state = scheduled_worker
        status_times, camera_times = [], []
        def status():
            status_times.append(state.now)
            return {}
        def snapshot():
            camera_times.append(state.now)
            if len(camera_times) == 2:
                raise OSError("camera unavailable")
            return {"image": "frame"}
        state.client.status.side_effect = status
        state.client.snapshot.side_effect = snapshot
        def wait(timeout):
            state.waits.append(timeout)
            if len(state.waits) == 1:
                state.now = 101
                state.monitor.frame_loaded(generation)
            elif len(state.waits) <= 3:
                state.now += timeout
                state.monitor.update(("printer", ""), playing=True)
            else:
                state.monitor.closed = True
        state.monitor.condition.wait = wait
        generation = state.monitor.update(("printer", ""), playing=True)
        state.monitor.run()
        assert state.waits == [3, 2, 1, 2]
        assert status_times == [100, 103]
        assert camera_times == [100, 101, 104]
        assert state.client.start_monitor.call_count == 2

    def test_camera_renews_while_the_previous_frame_awaits_acknowledgement(self, scheduled_worker):
        state = scheduled_worker
        renewals = []
        def renew():
            renewals.append(state.now)
            if len(renewals) == 2:
                state.monitor.closed = True
        def snapshot():
            for deadline in range(101, 160):
                state.now = deadline
                assert state.monitor.renew(("printer", ""), state.monitor.generation, playing=True)
            state.now = 159.5
            assert state.monitor.renew(("printer", ""), state.monitor.generation, playing=True)
            return {"image": "frame"}
        state.client.start_monitor.side_effect = renew
        state.client.snapshot.side_effect = snapshot
        def wait(timeout):
            state.waits.append(timeout)
            if len(state.waits) > 2:
                state.monitor.closed = True
                return
            state.now += timeout
            state.monitor.update(("printer", ""), playing=True)
        state.monitor.condition.wait = wait
        state.monitor.update(("printer", ""), playing=True)
        state.monitor.run()
        assert state.waits == [.5]
        assert renewals == [100, 160]
        state.client.snapshot.assert_called_once()

    def test_metadata_failure_preserves_the_next_status_phase(self, scheduled_worker):
        state = scheduled_worker
        def metadata():
            state.now += 1
            state.monitor.update(("printer", ""), playing=True)
            if state.client.refresh_metadata.call_count > 1:
                state.monitor.closed = True
            raise RuntimeError("metadata unavailable")
        state.client.refresh_metadata.side_effect = metadata
        state.monitor.update(("printer", ""), playing=True)
        state.monitor.run()
        assert state.waits == [2]
        state.client.status.assert_called_once()
        state.client.start_monitor.assert_not_called()
        state.client.snapshot.assert_not_called()
        kinds = ["status" if "printer" in message else "failure"
                 for message in state.messages if "printer" in message or "failed" in message]
        assert kinds == ["status", "failure"]

    def test_light_arriving_during_status_is_reconsidered_before_sleep(self, scheduled_worker):
        state = scheduled_worker
        def status():
            if state.client.status.call_count == 1:
                state.monitor.update(("printer", ""), True, playing=False)
            return {}
        state.client.status.side_effect = status
        state.monitor.update(("printer", ""), playing=False)
        state.monitor.run()
        state.client.set_light.assert_called_once_with(True)
        assert state.client.status.call_count == 2
        assert state.waits == [3]

    @pytest.mark.parametrize('failure', [
        pytest.param('activation', id='activation'),
        pytest.param('snapshot', id='snapshot'),
    ])
    def test_camera_failures_preserve_status_cadence_and_recover(self, failure):
        now = [100.0]
        messages, status_times, camera_times = [], [], []
        client = Mock()

        def status():
            assert not client.close.called
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
        assert status_times == [100, 103, 106, 109]
        assert sum((m.get('printer') == 'standby' for m in messages)) == 4
        assert camera_times == [100, 103, 106, 109]
        assert any((m.get('image') == 'frame' for m in messages))
        assert sum((m.get('failed', False) for m in messages)) == 3
        assert 'secret' not in str(messages)
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
                deadline = 159 if len(activations) == 1 else 160
                while now[0] < deadline:
                    now[0] += 1
                    assert monitor.renew(("printer", ""), monitor.generation, playing=True)
                monitor.frame_ready = True
            return {"image": "frame"}

        client.snapshot.side_effect = snapshot
        with patch.object(plugin.threading.Thread, "start"), \
                patch.object(plugin.time, "monotonic", side_effect=lambda: now[0]):
            monitor = plugin.Monitor(Mock(), lambda *target: client)
            monitor.update(("printer", ""), playing=True)
            monitor.run()
        assert activations == [1, 1, 2]
        client.close.assert_called_once()


class TestWorkerPolling:
    def advance(self, state, seconds):
        end = state.now + seconds
        while state.now < end:
            state.now = min(state.now + 1, end)
            assert state.monitor.renew(state.monitor.target, state.monitor.generation,
                                       playing=state.monitor.playing)

    def test_short_status_durations_do_not_accumulate_phase_drift(self, scheduled_worker):
        state = scheduled_worker
        starts, durations = [], [.25, .5, .75, .1]
        def status():
            starts.append(state.now)
            self.advance(state, durations[len(starts) - 1])
            return {}
        def wait(timeout):
            state.waits.append(timeout)
            if len(starts) == len(durations):
                state.monitor.closed = True
            else:
                self.advance(state, timeout)
        state.client.status.side_effect = status
        state.monitor.condition.wait = wait
        state.monitor.update(("printer", ""), playing=False)
        state.monitor.run()
        assert starts == [100, 103, 106, 109]
        assert state.waits == pytest.approx([2.75, 2.5, 2.25, 2.9])
        state.client.snapshot.assert_not_called()

    def test_long_status_skips_missed_ticks_without_a_catchup_burst(self, scheduled_worker):
        state = scheduled_worker
        starts, ends = [], []
        running = False
        def status():
            nonlocal running
            assert not running
            running = True
            starts.append(state.now)
            self.advance(state, 7 if len(starts) == 1 else .25)
            ends.append(state.now)
            running = False
            return {}
        def wait(timeout):
            state.waits.append(timeout)
            if len(starts) == 2:
                state.monitor.closed = True
            else:
                self.advance(state, timeout)
        state.client.status.side_effect = status
        state.monitor.condition.wait = wait
        state.monitor.update(("printer", ""), playing=False)
        state.monitor.run()
        assert starts == [100, 107]
        assert ends == [107, 107.25]
        assert state.waits == [1.75]

    @pytest.mark.parametrize("duration", [1, 7], ids=["short", "missed-ticks"])
    @pytest.mark.parametrize("fails", [False, True], ids=["metadata-success", "metadata-failure"])
    def test_metadata_cannot_hide_live_status_or_accumulate_poll_drift(self, scheduled_worker, duration, fails):
        state = scheduled_worker
        starts, events = [], []
        def status():
            starts.append(state.now)
            self.advance(state, .25)
            return {"printer": "standby"}
        def publish(data):
            state.messages.append(data)
            if "printer" in data:
                events.append("live-status")
        def metadata():
            events.append("metadata")
            if state.client.refresh_metadata.call_count == 1:
                self.advance(state, duration)
                if fails:
                    raise RuntimeError("metadata unavailable")
        def wait(timeout):
            state.waits.append(timeout)
            if len(starts) == 2:
                state.monitor.closed = True
            else:
                self.advance(state, timeout)
        state.monitor.publish = publish
        state.client.status.side_effect = status
        state.client.refresh_metadata.side_effect = metadata
        state.monitor.condition.wait = wait
        state.monitor.update(("printer", ""), playing=False)
        state.monitor.run()
        assert starts == [100, 103 if duration == 1 else 107.25]
        assert state.waits == ([1.75, 2.75] if duration == 1 else [1.5])
        assert events == ["live-status", "metadata", "live-status", "metadata"]
        assert sum(message.get("failed", False) for message in state.messages) == int(fails)
        state.client.start_monitor.assert_not_called()

    @pytest.mark.parametrize("operation", ["start_monitor", "snapshot"], ids=["activation", "snapshot"])
    def test_camera_overrun_allows_one_current_status_then_a_future_slot(self, scheduled_worker, operation):
        state = scheduled_worker
        starts = []
        def status():
            starts.append(state.now)
            self.advance(state, .25)
            return {}
        def camera():
            self.advance(state, 7)
            return {"image": "frame"}
        state.client.status.side_effect = status
        getattr(state.client, operation).side_effect = camera
        state.monitor.update(("printer", ""), playing=True)
        state.monitor.run()
        assert starts == [100, 107.25]
        assert state.waits == [1.5]
        state.client.start_monitor.assert_called_once()
        state.client.snapshot.assert_called_once()

    def test_status_errors_advance_the_same_phase_without_metadata_or_overlap(self, scheduled_worker):
        state = scheduled_worker
        starts = []
        def status():
            starts.append(state.now)
            self.advance(state, .75)
            raise TimeoutError("request expired")
        def wait(timeout):
            state.waits.append(timeout)
            if len(starts) == 3:
                state.monitor.closed = True
            else:
                self.advance(state, timeout)
        state.client.status.side_effect = status
        state.monitor.condition.wait = wait
        state.monitor.update(("printer", ""), playing=False)
        state.monitor.run()
        assert starts == [100, 103, 106]
        assert state.waits == [2.25] * 3
        assert [message["status_error"] for message in state.messages if "status_error" in message] == ["timeout"] * 3
        state.client.refresh_metadata.assert_not_called()

    def test_light_readback_resets_phase_after_the_command(self, scheduled_worker):
        state = scheduled_worker
        starts = []
        def status():
            starts.append(state.now)
            self.advance(state, .5)
            return {}
        def wait(timeout):
            state.waits.append(timeout)
            if len(starts) == 1:
                self.advance(state, 1)
                assert state.monitor.renew(("printer", ""), state.monitor.generation, True, playing=False)
            elif len(starts) == 2:
                self.advance(state, timeout)
            else:
                state.monitor.closed = True
        state.client.status.side_effect = status
        state.client.set_light.side_effect = lambda enabled: self.advance(state, .25)
        state.monitor.condition.wait = wait
        state.monitor.update(("printer", ""), playing=False)
        state.monitor.run()
        assert starts == [100, 101.75, 104.75]
        assert state.waits == [2.5] * 3
        state.client.set_light.assert_called_once_with(True)
        state.factory.assert_called_once()

    @pytest.mark.parametrize("change", ["target", "playback"])
    def test_new_session_resets_phase_instead_of_inheriting_the_previous_target(self, scheduled_worker, change):
        state = scheduled_worker
        starts = []
        def status():
            starts.append(state.now)
            self.advance(state, .25)
            return {}
        def wait(timeout):
            state.waits.append(timeout)
            if len(starts) == 1:
                self.advance(state, 1)
                target = ("second", "") if change == "target" else ("printer", "")
                state.monitor.update(target, playing=change == "playback")
            else:
                state.monitor.closed = True
        state.client.status.side_effect = status
        state.monitor.condition.wait = wait
        state.monitor.update(("printer", ""), playing=False)
        state.monitor.run()
        assert starts == [100, 101.25]
        assert state.waits == [2.75, 2.75]
        assert state.factory.call_count == 2
        assert state.monitor.generation == 2

    def test_lease_expiry_during_status_does_not_publish_or_start_a_new_poll(self, scheduled_worker):
        state = scheduled_worker
        def status():
            state.now += 4
            return {"printer": "standby"}
        state.client.status.side_effect = status
        state.monitor.update(("printer", ""), playing=False)
        state.monitor.run()
        assert state.waits == [300]
        state.client.status.assert_called_once()
        state.client.refresh_metadata.assert_not_called()
        state.client.close.assert_called_once()
        assert not any("printer" in message for message in state.messages)

    @pytest.mark.parametrize("duration", [3, 6], ids=["one-tick", "two-ticks"])
    def test_completion_on_a_tick_allows_one_current_poll_without_a_burst(self, scheduled_worker, duration):
        state = scheduled_worker
        starts = []
        def status():
            starts.append(state.now)
            if len(starts) == 1:
                self.advance(state, duration)
            elif len(starts) > 2:
                state.monitor.closed = True
            return {}
        state.client.status.side_effect = status
        state.monitor.update(("printer", ""), playing=False)
        state.monitor.run()
        assert starts == [100, 100 + duration]
        assert state.waits == [3]

    def test_overdue_poll_start_always_advances_to_a_strictly_future_phase_slot(self, scheduled_worker):
        state = scheduled_worker
        state.now = 2 ** 20 - 3.1
        phase = state.now
        starts = []
        def status():
            starts.append(state.now)
            if len(starts) > 2:
                state.monitor.closed = True
            return {}
        state.client.status.side_effect = status
        state.client.start_monitor.side_effect = lambda: self.advance(state, 6)
        state.monitor.update(("printer", ""), playing=True)
        state.monitor.run()
        assert starts == [phase, phase + 6]
        assert state.waits == pytest.approx([3])
        state.client.start_monitor.assert_called_once()
        state.client.snapshot.assert_called_once()


class TestWorker:

    @pytest.mark.parametrize('operation,unload', [(operation, unload) for operation in ('status', 'start_monitor', 'snapshot') for unload in (False, True)], ids=['status-target-change', 'status-unload', 'start_monitor-target-change', 'start_monitor-unload', 'snapshot-target-change', 'snapshot-unload'])
    def test_target_change_and_unload_interrupt_active_http(self, operation, unload):
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
                assert entered.wait(1)
                started = time.monotonic()
                if unload:
                    monitor.close()
                    assert not monitor.thread.is_alive()
                else:
                    monitor.update(("second", ""))
                    eventually(lambda: second.status.called)
                assert time.monotonic() - started < 0.7
        finally:
            monitor.close()
            peer.close()
        first.close.assert_called_once()

    @pytest.mark.parametrize("action", ["light", "frame", "target", "close"])
    def test_waiting_worker_rechecks_notifications_before_its_deadline(self, worker, action):
        monitor = worker.monitor
        waits = Queue()
        with monitor.condition:
            wait = monitor.condition.wait
            def observe(timeout=None):
                waits.put(timeout)
                return wait(timeout)
            monitor.condition.wait = observe
            generation = monitor.update(("first", ""), playing=True)
        timeout = waits.get(timeout=1)
        assert timeout > 1, "The next status deadline, rather than a polling tick, blocks the worker"
        first = worker.clients[0]
        assert first.snapshot.call_count == 1
        if action == "close":
            monitor.close()
            assert not monitor.thread.is_alive()
        else:
            if action == "light":
                monitor.update(("first", ""), True, playing=True)
            elif action == "frame":
                monitor.frame_loaded(generation)
            else:
                monitor.update(("second", ""), playing=True)
            assert waits.get(timeout=1) > 0
            if action == "light":
                first.set_light.assert_called_once_with(True)
                assert first.snapshot.call_count == 1
            elif action == "frame":
                assert first.snapshot.call_count == 2
            else:
                first.close.assert_called_once()
                assert len(worker.clients) == 2
                assert worker.clients[1].target == ("second", "")

    def test_pause_resume_target_change_and_unload(self, worker):
        worker.monitor.update(("first", ""), playing=True)
        eventually(lambda: any("image" in message for message in worker.messages))
        first = worker.clients[0]
        worker.monitor.update(None)
        eventually(lambda: first.close.called)
        count = len(worker.messages)
        time.sleep(0.05)
        assert len(worker.messages) == count
        generation = worker.monitor.update(("second", ""), True, playing=True)
        eventually(lambda: len(worker.clients) == 2 and worker.clients[1].snapshot.called)
        assert worker.clients[1].target == ('second', '')
        worker.clients[1].set_light.assert_called_once_with(True)
        assert worker.messages[-1]['generation'] == generation
        worker.monitor.close()
        assert not worker.monitor.thread.is_alive()

    def test_expired_lease_stops_client(self, worker):
        worker.monitor.update(("printer", ""), playing=True)
        eventually(lambda: bool(worker.clients))
        with worker.monitor.condition:
            worker.monitor.expires = 0
            worker.monitor.condition.notify()
        eventually(lambda: worker.clients[0].close.called)

    def test_no_old_frame_after_target_change(self, worker):
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

        worker.monitor.client_factory = factory
        worker.monitor.update(("old", ""), playing=True)
        assert entered.wait(timeout=2)
        worker.monitor.update(("new", ""), playing=True)
        release.set()
        eventually(lambda: any(m.get("image") == "new" for m in worker.messages))
        assert not any((m.get('image') == 'old' for m in worker.messages))

    def test_errors_are_visible_and_secret_urls_are_not(self, worker):
        def fail(*target):
            raise OSError("http://printer?token=secret")
        worker.monitor.client_factory = fail
        worker.monitor.update(("printer", ""))
        eventually(lambda: any(m.get("failed") for m in worker.messages))
        assert worker.messages[-1]['failed']
        assert 'secret' not in str(worker.messages)

    def test_frames_wait_for_display_acknowledgement(self, worker):
        generation = worker.monitor.update(("printer", ""), playing=True)
        eventually(lambda: any("image" in m for m in worker.messages))
        client = worker.clients[0]
        worker.monitor.update(("printer", ""), playing=True)
        time.sleep(0.05)
        assert client.snapshot.call_count == 1
        worker.monitor.frame_loaded(generation - 1)
        time.sleep(0.05)
        assert client.snapshot.call_count == 1
        worker.monitor.frame_loaded(generation)
        eventually(lambda: client.snapshot.call_count == 2)

    def test_generation_is_announced_before_frames_on_play_and_target_change(self, worker):
        announced = set()
        displayed = []

        def display(message):
            worker.messages.append(message)
            if message.get("type") == "target":
                announced.add(message["generation"])
            elif "image" in message and message["generation"] in announced:
                displayed.append(message)
                # Model the page: only frames from a known generation are acknowledged.
                if sum(m["generation"] == message["generation"] for m in displayed) == 1:
                    worker.monitor.frame_loaded(message["generation"])

        worker.monitor.publish = display
        for target, playing in [(("first", ""), False), (("first", ""), True),
                                (("second", ""), True)]:
            generation = worker.monitor.update(target, playing=playing)
            assert generation in announced
            if playing:
                eventually(lambda: sum(m["generation"] == generation for m in displayed) == 2)

    def test_light_works_without_camera_playback(self, worker):
        worker.monitor.update(("printer", ""), True, playing=False)
        eventually(lambda: any("light" in m for m in worker.messages))
        client = worker.clients[0]
        client.set_light.assert_called_once_with(True)
        client.start_monitor.assert_not_called()
        client.snapshot.assert_not_called()

    def test_status_failure_does_not_interrupt_camera(self, worker):
        client = Mock()
        client.status.side_effect = TimeoutError("token=secret")
        client.snapshot.return_value = {"image": "frame"}
        worker.monitor.client_factory = lambda *target: client
        worker.monitor.update(("printer", ""), playing=True)
        eventually(lambda: any("image" in m for m in worker.messages))
        assert any((m.get('status_error') == 'timeout' for m in worker.messages))
        assert not any((m.get('failed') for m in worker.messages))
        assert 'secret' not in str(worker.messages)
        client.refresh_metadata.assert_not_called()

    def test_light_failure_does_not_interrupt_camera_or_expose_exception_details(self, worker):
        generation = worker.monitor.update(("printer", ""), playing=True)
        eventually(lambda: any("image" in m for m in worker.messages))
        client = worker.clients[0]
        client.set_light.side_effect = OSError("http://printer?token=secret")
        worker.monitor.update(("printer", ""), True, playing=True)
        eventually(lambda: any("light_error" in m for m in worker.messages))
        worker.monitor.frame_loaded(generation)
        eventually(lambda: client.snapshot.call_count == 2)
        assert not any((m.get('failed') for m in worker.messages))
        assert 'secret' not in str(worker.messages)
        assert not client.close.called

    def test_explicit_stop_closes_camera_but_keeps_status_available(self, worker):
        generation = worker.monitor.update(("printer", ""), playing=True)
        eventually(lambda: any("image" in m for m in worker.messages))
        first = worker.clients[0]
        worker.monitor.update(("printer", ""), playing=False)
        worker.monitor.frame_loaded(generation)
        eventually(lambda: len(worker.clients) == 2 and worker.clients[1].status.called)
        first.close.assert_called_once()
        worker.clients[1].start_monitor.assert_not_called()
        worker.clients[1].snapshot.assert_not_called()


class TestHeartbeat:
    def test_unchanged_activity_resolves_only_at_the_fixed_refresh_deadline(self, heartbeat_page):
        state = heartbeat_page
        for second in range(11):
            state.now = 100 + second
            state.activity()
        assert state.resolve.call_count == 6
        assert state.saved.call_count == 6
        assert len(state.targets) == 1
        assert state.monitor.generation == state.targets[0]["generation"]
        assert state.monitor.expires == 113.5

    def test_selected_json_is_read_only_at_the_fixed_refresh_deadline(self, heartbeat_page, tmp_path):
        state = heartbeat_page
        selected = tmp_path / "selected.json"
        selected.write_text(json.dumps({"print_host": "saved.local", "printhost_apikey": "saved-key"}))
        preset = orca.host.preset_bundle().current_printer_preset()
        preset.file = str(selected)
        state.saved.side_effect = state.saved_function
        reads = []
        open_file = Path.open
        def read(path, *args, **kwargs):
            if path == selected:
                reads.append(state.now)
            return open_file(path, *args, **kwargs)
        with patch.object(Path, "open", read):
            for second in range(11):
                state.now = 100 + second
                state.activity()
        assert reads == [100, 102, 104, 106, 108, 110]
        assert state.monitor.target == ("http://saved.local", "saved-key")
        assert len(state.targets) == 1

    def test_absent_target_uses_a_bounded_negative_resolution(self, heartbeat_page):
        state = heartbeat_page
        state.saved.return_value = ("", "")
        for second in range(11):
            state.now = 100 + second
            state.activity()
        assert state.resolve.call_count == 6
        assert state.saved.call_count == 6
        assert not state.targets
        assert state.monitor.target is None
        state.config["host"] = "manual.local"
        state.activity()
        assert state.monitor.target == ("http://manual.local", "")

    def test_manual_config_change_during_consent_stops_old_session_before_prompt(self, heartbeat_page):
        state = heartbeat_page
        state.activity()
        state.config.clear()
        preset = orca.host.preset_bundle().current_printer_preset()
        preset.config_value.reset_mock()
        def confirm(*args, **kwargs):
            assert state.monitor.target is None
            state.config["host"] = "manual.local"
            return "yes"
        orca.host.ui.message.side_effect = confirm
        state.activity()
        assert state.monitor.target is None
        assert not any(call.args[0] in ("print_host", "printhost_apikey")
                       for call in preset.config_value.call_args_list)
        state.activity()
        assert state.monitor.target == ("http://manual.local", "")

    def test_config_change_invalidates_before_resolution_and_keeps_manual_host_keyless(self, heartbeat_page):
        state = heartbeat_page
        state.activity()
        previous = state.monitor.generation
        state.config["host"] = "manual.local"
        state.activity()
        assert state.monitor.target == ("http://manual.local", "")
        assert state.monitor.generation > previous
        assert len(state.targets) == 2
        assert state.saved.call_count == 1
        state.saved.assert_called_once()

    @pytest.mark.parametrize("field,value", [
        pytest.param("print_host", "changed.local", id="address"),
        pytest.param("printhost_apikey", "changed-key", id="key"),
    ])
    def test_api_connection_changes_refresh_by_the_bounded_fallback(self, heartbeat_page, field, value):
        state = heartbeat_page
        state.values.update(print_host="printer.local", printhost_apikey="initial-key")
        state.activity()
        previous = state.monitor.generation
        state.values[field] = value
        state.now += 1
        state.activity()
        assert state.monitor.generation == previous
        state.now += 1
        state.activity()
        assert state.monitor.target == ("http://changed.local" if field == "print_host" else "http://printer.local",
                                        value if field == "printhost_apikey" else "initial-key")
        assert state.monitor.generation > previous
        assert len(state.targets) == 2

    def test_saved_connection_changes_refresh_without_a_selection_event(self, heartbeat_page):
        state = heartbeat_page
        state.activity()
        state.saved.return_value = ("changed.local", "changed-key")
        state.now += 2
        state.activity()
        assert state.monitor.target == ("http://changed.local", "changed-key")
        assert len(state.targets) == 2

    @pytest.mark.parametrize("allowed", [False, None])
    def test_permission_revocation_blocks_reads_and_old_frame_renewals(self, heartbeat_page, allowed):
        state = heartbeat_page
        state.activity(playing=True)
        generation = state.monitor.generation
        state.config["allow_preset_connection"] = allowed
        state.activity()
        assert state.monitor.target is None
        calls = state.saved.call_count
        expires = state.monitor.expires
        state.page.on_message({"type": "frame", "generation": generation})
        assert state.monitor.expires == expires
        assert state.saved.call_count == calls == 1
        assert state.page.post_message.call_args.args[0]["error"]

    def test_restored_permission_recovers_with_a_fresh_page_handshake(self, heartbeat_page):
        state = heartbeat_page
        state.activity()
        old_generation = state.monitor.generation
        state.config["allow_preset_connection"] = False
        state.activity()
        assert state.monitor.target is None
        state.config["allow_preset_connection"] = True
        state.page.on_message({"type": "activity", "active": True, "playing": False})
        assert state.monitor.target == ("http://printer.local", "initial-key")
        assert state.monitor.generation > old_generation
        expires = state.monitor.expires
        state.page.on_message({"type": "activity", "active": True, "playing": True,
                               "generation": old_generation})
        assert state.monitor.expires == expires
        assert not state.monitor.playing

    def test_frames_refresh_permissions_even_without_activity_ticks(self, heartbeat_page):
        state = heartbeat_page
        state.activity(playing=True)
        generation = state.monitor.generation
        state.config["allow_preset_connection"] = False
        state.now += 2.01
        state.page.on_message({"type": "frame", "generation": generation})
        assert state.monitor.target is None
        assert state.saved.call_count == 1

    def test_expired_frame_cannot_reactivate_a_session(self, heartbeat_page):
        state = heartbeat_page
        state.activity(playing=True)
        generation = state.monitor.generation
        state.now += 3.5
        expires = state.monitor.expires
        state.page.on_message({"type": "frame", "generation": generation})
        assert state.monitor.expires == expires
        assert not state.monitor.current(generation)
        state.activity()
        assert state.monitor.current(state.monitor.generation)
        assert state.monitor.generation > generation
        assert len(state.targets) == 2

    def test_stale_activity_and_light_cannot_renew_or_command_a_new_generation(self, heartbeat_page):
        state = heartbeat_page
        state.activity()
        generation = state.monitor.generation
        state.page.on_lifecycle_event(orca.LifecycleEvent.DeviceSelected, types.SimpleNamespace())
        expires = state.monitor.expires
        for message in [{"type": "activity", "active": True, "playing": True},
                        {"type": "activity", "active": False},
                        {"type": "light", "enabled": True}]:
            state.page.on_message(dict(message, generation=generation))
        assert state.monitor.target is None
        assert state.monitor.expires == expires
        assert state.monitor.light is None
        assert state.resolve.call_count == 1

    @pytest.mark.parametrize("event", ["DeviceSelected", "PresetSelected", "PresetSaved"])
    def test_supported_events_invalidate_and_resolve_on_the_next_ui_callback(self, heartbeat_page, event):
        state = heartbeat_page
        state.activity()
        state.saved.return_value = ("selected.local", "selected-key")
        state.page.on_lifecycle_event(getattr(orca.LifecycleEvent, event), types.SimpleNamespace(msg="printer"))
        assert state.monitor.target is None
        state.page.on_message({"type": "activity", "active": True, "playing": False})
        assert state.monitor.target == ("http://selected.local", "selected-key")
        assert state.saved.call_count == 2

    def test_hide_show_reload_and_playback_transitions_reannounce_before_work(self, heartbeat_page):
        state = heartbeat_page
        state.activity()
        first = state.monitor.generation
        state.activity(playing=True)
        assert state.monitor.generation > first
        assert len(state.targets) == 2
        state.page.on_message({"type": "activity", "active": False, "generation": state.monitor.generation})
        assert state.monitor.target is None
        state.page.on_message({"type": "activity", "active": True, "playing": False})
        assert len(state.targets) == 3
        state.page.get_ui()
        assert state.monitor.target is None
        state.page.on_message({"type": "activity", "active": True, "playing": False})
        assert len(state.targets) == 4
        state.page.on_unload()
        assert state.page.monitor is None

    def test_light_command_uses_validated_session_without_resolution_or_announcement(self, heartbeat_page):
        state = heartbeat_page
        state.activity()
        state.page.on_message({"type": "light", "enabled": True, "generation": state.monitor.generation})
        assert state.monitor.light is True
        assert state.resolve.call_count == 1
        assert len(state.targets) == 1

    def test_unchanged_direct_update_does_not_repeat_the_target_announcement(self, heartbeat_page):
        state = heartbeat_page
        state.activity()
        state.monitor.update(state.monitor.target, playing=False)
        assert len(state.targets) == 1

    @pytest.mark.parametrize("kind", ["activity", "light"])
    def test_selection_at_renewal_boundary_cannot_restore_or_command_the_old_target(self, heartbeat_page, kind):
        state = heartbeat_page
        state.activity()
        generation = state.monitor.generation
        def renew(*args):
            state.page.on_lifecycle_event(orca.LifecycleEvent.DeviceSelected, types.SimpleNamespace())
            return False
        state.monitor.renew = renew
        state.page.on_message({"type": kind, "generation": generation, "active": True,
                               "playing": False, "enabled": True})
        assert state.monitor.target is None
        assert state.monitor.light is None
        assert len(state.targets) == 1

    def test_selection_at_initial_application_boundary_cannot_restore_the_old_target(self, heartbeat_page):
        state = heartbeat_page
        activity_target = state.page.activity_target
        def resolve(*args, **kwargs):
            result = activity_target(*args, **kwargs)
            state.page.on_lifecycle_event(orca.LifecycleEvent.DeviceSelected, types.SimpleNamespace())
            return result
        state.page.activity_target = resolve
        state.activity()
        assert state.monitor.target is None
        assert not state.targets

    def test_revocation_during_address_lookup_prevents_key_reads(self, heartbeat_page):
        state = heartbeat_page
        preset = orca.host.preset_bundle().current_printer_preset()
        def value(name):
            if name == "print_host":
                state.config["allow_preset_connection"] = False
                return "printer.local"
            return state.values.get(name)
        preset.config_value.side_effect = value
        state.activity()
        assert state.monitor.target is None
        assert "printhost_apikey" not in [call.args[0] for call in preset.config_value.call_args_list]
        assert not state.targets

    def test_config_change_during_resolution_is_not_cached_against_the_old_target(self, heartbeat_page):
        state = heartbeat_page
        def resolve(*args):
            state.config["host"] = "manual.local"
            return ("http://obsolete.local", "obsolete-key")
        state.resolve.side_effect = resolve
        state.activity()
        assert state.monitor.target is None
        assert not state.targets
        assert state.page.post_message.call_args.args[0]["error"]

    def test_selection_event_during_resolution_cannot_restart_the_old_target(self, heartbeat_page):
        state = heartbeat_page
        def resolve(*args):
            state.page.on_lifecycle_event(orca.LifecycleEvent.DeviceSelected, types.SimpleNamespace())
            return ("http://obsolete.local", "obsolete-key")
        state.resolve.side_effect = resolve
        state.activity()
        assert state.monitor.target is None
        assert not state.targets


class TestTarget:

    @pytest.mark.parametrize('choice', [
        pytest.param('yes', id='yes'),
        pytest.param('no', id='no'),
        pytest.param('cancel', id='cancel'),
    ])
    def test_preset_connection_reads_require_consent_and_remember_the_decision(self, make_page, choice):
        page = make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local",
                               "printhost_apikey": "secret"}, {})
        orca.host.ui.message.return_value = choice
        with patch.object(plugin, "saved_connection") as read_file:
            for _ in range(2):
                if choice == "yes":
                    assert page.selected_target() == ('http://printer.local', 'secret')
                else:
                    with pytest.raises(ValueError, match='access is disabled'):
                        page.selected_target()
            read_file.assert_not_called()
        orca.host.ui.message.assert_called_once()
        page.save_config.assert_called_once_with(json.dumps({
            "allow_preset_connection": choice == "yes",
        }))
        if choice != "yes":
            preset = orca.host.preset_bundle().current_printer_preset()
            assert 'print_host' not in [call.args[0] for call in preset.config_value.call_args_list]
            assert 'printhost_apikey' not in [call.args[0] for call in preset.config_value.call_args_list]

    def test_saved_denial_skips_prompt_and_file_read(self, make_page):
        page = make_page({"printer_model": "Snapmaker U1"}, {"allow_preset_connection": False})
        with patch.object(plugin, "saved_connection") as read_file:
            with pytest.raises(ValueError, match='access is disabled'):
                page.selected_target()
            read_file.assert_not_called()
        orca.host.ui.message.assert_not_called()

    def test_cleared_saved_approval_does_not_fall_back_to_cached_yes(self, make_page):
        config = {}
        page = make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"}, config)
        orca.host.ui.message.return_value = "yes"
        assert page.selected_target() == ('http://printer.local', '')
        config.clear()
        orca.host.ui.message.return_value = "no"
        with pytest.raises(ValueError, match='access is disabled'):
            page.selected_target()
        assert orca.host.ui.message.call_count == 2

    def test_one_approval_covers_future_u1_preset_selections(self, make_page):
        values = {"printer_model": "Snapmaker U1", "print_host": "first.local", "printhost_apikey": "first-key"}
        page = make_page(values, {}, file="/profiles/first.json")
        orca.host.ui.message.return_value = "yes"
        assert page.selected_target() == ('http://first.local', 'first-key')
        preset = orca.host.preset_bundle().current_printer_preset()
        preset.file = "/profiles/second.json"
        values.update(print_host="second.local", printhost_apikey="second-key")
        page.on_lifecycle_event(orca.LifecycleEvent.PresetSelected, types.SimpleNamespace(msg="printer"))
        assert page.selected_target() == ('http://second.local', 'second-key')
        orca.host.ui.message.assert_called_once()

    def test_revocation_blocks_connection_reads_after_selection(self, make_page):
        config = {}
        page = make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local",
                               "printhost_apikey": "key"}, config)
        orca.host.ui.message.return_value = "yes"
        page.selected_target()
        config["allow_preset_connection"] = False
        preset = orca.host.preset_bundle().current_printer_preset()
        preset.config_value.reset_mock()
        page.monitor = Mock(condition=threading.Condition(), target=None, generation=0)
        page.on_lifecycle_event(orca.LifecycleEvent.PresetSelected, types.SimpleNamespace(msg="printer"))
        with patch.object(plugin, "saved_connection") as read_file:
            page.on_message({"type": "activity", "active": True, "playing": True})
            read_file.assert_not_called()
        page.monitor.update.assert_called_with(None)
        assert 'print_host' not in [call.args[0] for call in preset.config_value.call_args_list]
        assert 'printhost_apikey' not in [call.args[0] for call in preset.config_value.call_args_list]

    def test_existing_global_approval_does_not_prompt_again(self, make_page):
        page = make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"},
                              {"allow_preset_connection": True})
        assert page.selected_target() == ('http://printer.local', '')
        orca.host.ui.message.assert_not_called()

    def test_failed_permission_save_does_not_allow_connection_reads(self, make_page):
        page = make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"}, {})
        page.save_config.side_effect = None
        page.save_config.return_value = False
        orca.host.ui.message.return_value = "yes"
        with pytest.raises(ValueError, match='(?:save|confirm).*access choice'):
            page.selected_target()
        preset = orca.host.preset_bundle().current_printer_preset()
        assert 'print_host' not in [call.args[0] for call in preset.config_value.call_args_list]

    def test_saved_approval_must_be_visible_in_effective_config(self, make_page):
        page = make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"}, {})
        # A preset override can hide a successful global save from get_config().
        page.get_config = lambda: "{}"
        orca.host.ui.message.return_value = "yes"
        with pytest.raises(ValueError, match='(?:save|confirm).*access choice'):
            page.selected_target()
        with pytest.raises(ValueError, match='access is disabled'):
            page.selected_target()
        orca.host.ui.message.assert_called_once()
        preset = orca.host.preset_bundle().current_printer_preset()
        assert 'print_host' not in [call.args[0] for call in preset.config_value.call_args_list]

    def test_selection_change_during_consent_cannot_mix_connections(self, make_page):
        values = {"printer_model": "Snapmaker U1", "print_host": "first.local"}
        page = make_page(values, {}, file="/profiles/first.json")
        preset = orca.host.preset_bundle().current_printer_preset()

        def confirm(*args, **kwargs):
            preset.file = "/profiles/second.json"
            values.update(print_host="second.local", printhost_apikey="unapproved-key")
            return "yes"

        orca.host.ui.message.side_effect = confirm
        with pytest.raises(ValueError, match='selection changed'):
            page.selected_target()
        assert 'print_host' not in [call.args[0] for call in preset.config_value.call_args_list]
        assert 'printhost_apikey' not in [call.args[0] for call in preset.config_value.call_args_list]

    def test_disabling_access_during_consent_cannot_be_overwritten_by_yes(self, make_page):
        config = {}
        page = make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"}, config)

        def confirm(*args, **kwargs):
            config["allow_preset_connection"] = False
            return "yes"

        orca.host.ui.message.side_effect = confirm
        with pytest.raises(ValueError, match='access is disabled'):
            page.selected_target()
        page.save_config.assert_not_called()
        preset = orca.host.preset_bundle().current_printer_preset()
        assert 'print_host' not in [call.args[0] for call in preset.config_value.call_args_list]

    def test_pending_consent_blocks_reentrant_page_messages(self, make_page):
        page = make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local"}, {})

        def confirm(*args, **kwargs):
            with pytest.raises(ValueError, match='access is disabled'):
                page.selected_target()
            return "yes"

        orca.host.ui.message.side_effect = confirm
        assert page.selected_target() == ('http://printer.local', '')
        orca.host.ui.message.assert_called_once()

    def test_consent_covers_the_selected_file_fallback(self, make_page):
        page = make_page({"printer_model": "Snapmaker U1"}, {})
        orca.host.ui.message.return_value = "yes"
        with patch.object(plugin, "saved_connection", return_value=("printer.local", "key")) as read_file:
            assert page.selected_target() == ('http://printer.local', 'key')
            read_file.assert_called_once()
        orca.host.ui.message.assert_called_once()

    def test_selected_preset_and_manual_host_credential_isolation(self, make_page):
        page = make_page({"printer_model": "Snapmaker U1", "print_host": "printer.local",
                               "printhost_apikey": "secret"})
        assert page.selected_target() == ('http://printer.local', 'secret')
        page.get_config = lambda: '{"host":"http://other.local"}'
        assert page.selected_target() == ('http://other.local', '')

    def test_saved_selected_preset_supplies_address_and_its_own_key(self, tmp_path, make_page):
        directory = str(tmp_path)
        path = Path(directory) / "selected.json"
        path.write_text(json.dumps({"print_host": "saved-printer.local:7125", "printhost_apikey": "saved-key"}))
        page = make_page({"printer_model": "Snapmaker U1", "printhost_apikey": "unrelated-key"}, file=str(path))
        assert page.selected_target() == ('http://saved-printer.local:7125', 'saved-key')

    def test_manual_host_works_with_other_presets_without_reading_their_credentials(self, make_page):
        page = make_page({"printer_model": "Bambu Lab A1", "printhost_apikey": "unrelated-key"},
                              {"host": "u1.local"})
        assert page.selected_target() == ('http://u1.local', '')
        with patch.object(orca.host, "preset_bundle", side_effect=RuntimeError("No preset available")):
            assert page.selected_target() == ('http://u1.local', '')
        page.monitor = Mock(condition=threading.Condition(), target=None, generation=0)
        page.on_message({"type": "activity", "active": True, "playing": True})
        page.monitor.update.assert_called_once_with(("http://u1.local", ""), None, True)
        page.get_config = lambda: '{"host":"file:///tmp/invalid"}'
        with pytest.raises(ValueError):
            page.selected_target()

    def test_other_printer_without_manual_host_reports_address_warning(self, make_page):
        page = make_page({"printer_model": "Bambu Lab A1", "print_host": "other-printer.local"})
        page.monitor = Mock(condition=threading.Condition(), target=None, generation=0)
        for message in [{"type": "activity", "active": True, "playing": True},
                        {"type": "light", "enabled": True}]:
            page.on_message(message)
            page.monitor.update.assert_called_with(None)
            assert not page.post_message.call_args.args[0]['supported']
            assert page.post_message.call_args.args[0]['error'], 'An unavailable target includes an explanation'

    def test_u1_without_address_reports_configuration_warning(self, make_page):
        page = make_page({"printer_model": "Snapmaker U1"})
        page.monitor = Mock(condition=threading.Condition(), target=None, generation=0)
        page.on_message({"type": "activity", "active": True, "playing": True})
        page.monitor.update.assert_called_once_with(None)
        assert page.post_message.call_args.args[0]['supported']
        assert page.post_message.call_args.args[0]['error'], 'An unavailable target includes an explanation'

    def test_inherited_u1_model_and_similar_names(self):
        preset = Mock()
        preset.name = "Quiet printer"
        preset.config_value.side_effect = {"inherits": "Snapmaker U1 (0.4 nozzle)"}.get
        assert plugin.is_u1_preset(preset)
        preset.config_value.side_effect = {"printer_model": "Snapmaker U10"}.get
        assert not plugin.is_u1_preset(preset)
        preset.name = "Snapmaker U1 copy"
        preset.config_value.side_effect = {"printer_model": "Other printer"}.get
        assert not plugin.is_u1_preset(preset)

    def test_printer_selection_stops_monitor_and_refreshes_page(self):
        page = plugin.U1Page()
        page.monitor = Mock(condition=threading.Condition(), target=None, generation=0)
        page.post_message = Mock()
        page.on_lifecycle_event(orca.LifecycleEvent.PresetSelected, types.SimpleNamespace(msg="printer"))
        page.monitor.update.assert_called_once_with(None)
        page.post_message.assert_called_once_with({"type": "selection_changed"})
        page.monitor.reset_mock()
        page.on_lifecycle_event(orca.LifecycleEvent.PresetSelected, types.SimpleNamespace(msg="filament"))
        page.monitor.update.assert_not_called()





def estimate_status():
        return {"print_stats": {"state": "printing", "filename": "part.gcode",
                                "print_duration": 3000, "total_duration": 3100,
                                "info": {"current_layer": 30, "total_layer": 100}},
                "display_status": {"progress": .36},
                "virtual_sdcard": {"progress": .30966, "file_position": 300, "file_size": 1000},
                "gcode_move": {"speed_factor": 1}}


@pytest.fixture(scope="module")
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01))
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)
        assert not thread.is_alive()


@pytest.fixture
def http_endpoint(http_server, monkeypatch):
    monkeypatch.setattr(Endpoint, "requests", [])
    monkeypatch.setattr(Endpoint, "payloads", [])
    monkeypatch.setattr(Endpoint, "status_code", 200)
    monkeypatch.setattr(Endpoint, "status_body", None)
    sockets = []
    connect = plugin.NetworkOperation.connect

    def track_socket(operation, *args):
        sock = connect(operation, *args)
        sockets.append(sock)
        return sock

    monkeypatch.setattr(plugin.NetworkOperation, "connect", track_socket)
    try:
        yield "http://127.0.0.1:%d" % http_server.server_port
    finally:
        # Mock WebSockets do not own the real connection passed into their fake handshake.
        for sock in sockets:
            sock.close()


@pytest.fixture
def websocket_server():
    servers = []

    def serve(redirect_status=0, redirect_url=""):
        server = WebSocketServer(("127.0.0.1", 0), WebSocketEndpoint)
        server.connections = 0
        server.requests = []
        server.redirect_status = redirect_status
        server.redirect_url = redirect_url
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01))
        thread.start()
        servers.append((server, thread))
        return server

    try:
        yield serve
    finally:
        for server, thread in reversed(servers):
            server.shutdown()
            server.server_close()
            thread.join(1)
            assert not thread.is_alive()


@pytest.fixture
def scheduled_worker(monkeypatch):
    state = types.SimpleNamespace(now=100.0, waits=[], messages=[])
    state.client = Mock()
    state.client.status.return_value = {"printer": "standby"}
    state.client.snapshot.return_value = {"image": "frame"}
    state.factory = Mock(return_value=state.client)
    monkeypatch.setattr(plugin, "time", types.SimpleNamespace(monotonic=lambda: state.now))
    monkeypatch.setattr(plugin, "threading", types.SimpleNamespace(
        Condition=threading.Condition, Thread=lambda **kwargs: Mock()))
    state.monitor = plugin.Monitor(state.messages.append, state.factory)
    def wait(timeout=None):
        state.waits.append(timeout)
        state.monitor.closed = True
    state.monitor.condition.wait = wait
    return state


@pytest.fixture
def worker():
    clients, messages = [], []

    def factory(*target):
        client = Mock()
        client.target = target
        client.status.return_value = {"printer": "standby", "light": False}
        client.snapshot.return_value = {"image": "frame"}
        clients.append(client)
        return client

    monitor = plugin.Monitor(messages.append, factory)
    try:
        yield types.SimpleNamespace(monitor=monitor, clients=clients, messages=messages)
    finally:
        monitor.close()
        assert not monitor.thread.is_alive()


@pytest.fixture
def make_page(monkeypatch):
    def create(values, config=None, file=""):
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
        monkeypatch.setattr(orca, "host", types.SimpleNamespace(preset_bundle=lambda: bundle,
                            ui=types.SimpleNamespace(message=Mock(return_value="no"))), raising=False)
        return page

    return create


@pytest.fixture
def heartbeat_page(make_page, monkeypatch):
    state = types.SimpleNamespace(now=100.0, values={"printer_model": "Snapmaker U1"},
                                  config={"allow_preset_connection": True}, targets=[])
    monkeypatch.setattr(plugin, "time", types.SimpleNamespace(monotonic=lambda: state.now))
    monkeypatch.setattr(plugin, "threading", types.SimpleNamespace(
        Condition=threading.Condition, Thread=lambda **kwargs: Mock()))
    state.page = make_page(state.values, state.config, file="/profiles/selected.json")
    state.monitor = plugin.Monitor(state.targets.append)
    state.page.monitor = state.monitor
    state.saved_function = plugin.saved_connection
    state.saved = Mock(return_value=("printer.local", "initial-key"))
    monkeypatch.setattr(plugin, "saved_connection", state.saved)
    state.resolve = Mock(wraps=state.page.selected_target)
    monkeypatch.setattr(state.page, "selected_target", state.resolve)
    def activity(playing=False):
        state.page.on_message({"type": "activity", "active": True, "playing": playing,
                               "generation": state.monitor.generation} if state.monitor.target else
                              {"type": "activity", "active": True, "playing": playing})
    state.activity = activity
    yield state
    state.monitor.close()
