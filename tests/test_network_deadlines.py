"""Elapsed deadlines with real HTTP and WebSocket sockets, without a printer."""
import importlib.util
from pathlib import Path
import socket
import sys
import threading
import time
import pytest
from unittest.mock import patch

import websocket

spec = importlib.util.spec_from_file_location('deadline_http_client', Path(__file__).parents[1] / 'src/http_client.py')
transport = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = transport
spec.loader.exec_module(transport)


class TestDeadline:
    def slow_peer(self, peer, data, interval=.04):
        try:
            for byte in data:
                peer.sendall(bytes([byte]))
                time.sleep(interval)
        except OSError:
            pass
        finally:
            peer.close()

    def assert_bounded(self, action):
        started = time.monotonic()
        with pytest.raises((OSError, websocket.WebSocketException)):
            action()
        assert time.monotonic() - started < 0.8

    @pytest.mark.parametrize('data', [b'HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\n', b'HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\n' + b'x' * 50], ids=['trickled-headers', 'trickled-body'])
    def test_http_trickled_headers_and_body_have_total_deadline(self, data):
        local, peer = socket.socketpair()
        def server():
            peer.recv(4096)
            if data.endswith(b'x' * 50):
                peer.sendall(data[:-50])
                self.slow_peer(peer, data[-50:])
            else:
                self.slow_peer(peer, data)
        thread = threading.Thread(target=server)
        thread.start()
        client = transport.HttpClient('http://printer')
        def connect(operation, *args):
            return operation.track(local)
        with patch.object(transport, 'NETWORK_TIMEOUT', .15), \
                patch.object(transport.NetworkOperation, 'connect', connect):
            self.assert_bounded(lambda: client.request('/'))
        client.close()
        thread.join(1)
        assert not thread.is_alive()

    @pytest.mark.parametrize('payload', [b'\x81~\x00d' + b'x' * 100, b'\x89\x00' * 100], ids=['trickled-frame', 'control-stream'])
    def test_websocket_trickled_frame_and_control_stream_have_total_deadline(self, payload):
        local, peer = socket.socketpair()
        ws = websocket.WebSocket()
        ws.sock = local
        ws.connected = True
        client = transport.HttpClient('http://printer')
        client.ws = ws
        thread = threading.Thread(target=self.slow_peer, args=(peer, payload, .01))
        thread.start()
        with patch.object(transport, 'NETWORK_TIMEOUT', .15):
            self.assert_bounded(client.start_monitor)
        assert client.ws is None
        assert not client.closed
        client.close()
        thread.join(1)
        assert not thread.is_alive()

    @pytest.mark.parametrize('scheme', ('http', 'https'), ids=['http', 'https'])
    def test_websocket_http_and_tls_handshakes_have_total_deadline(self, scheme):
        listener = socket.create_server(('127.0.0.1', 0))
        def serve():
            peer, _ = listener.accept()
            peer.recv(4096)
            self.slow_peer(peer, b'HTTP/1.1 101 Switching Protocols\r\n' * 10)
        thread = threading.Thread(target=serve)
        thread.start()
        client = transport.HttpClient('%s://127.0.0.1:%d' % (scheme, listener.getsockname()[1]))
        try:
            with patch.object(transport, 'NETWORK_TIMEOUT', .15):
                self.assert_bounded(client.start_monitor)
        finally:
            client.close()
            thread.join(1)
            listener.close()
        assert not thread.is_alive()

    def test_close_interrupts_blocked_http_before_deadline(self):
        local, peer = socket.socketpair()
        reading = threading.Event()
        def connect(operation, *args):
            reading.set()
            return operation.track(local)
        client = transport.HttpClient('http://printer')
        failures = []
        def run():
            try:
                client.request('/')
            except Exception as error:
                failures.append(error)
        with patch.object(transport.NetworkOperation, 'connect', connect):
            thread = threading.Thread(target=run)
            thread.start()
            assert reading.wait(1)
            started = time.monotonic()
            client.close()
            thread.join(.5)
            assert not thread.is_alive()
            assert time.monotonic() - started < 0.5
        peer.close()
        assert len(failures) == 1
        with pytest.raises(TimeoutError):
            client.request('/')

    def test_close_interrupts_websocket_receive(self):
        local, peer = socket.socketpair()
        ws = websocket.WebSocket()
        ws.sock = local
        ws.connected = True
        client = transport.HttpClient('http://printer')
        client.ws = ws
        finished = threading.Event()
        def receive():
            try:
                client.start_monitor()
            except (OSError, websocket.WebSocketException):
                pass
            finally:
                finished.set()
        thread = threading.Thread(target=receive)
        thread.start()
        peer.recv(4096)  # The command has been sent and the socket is tracked.
        client.close()
        assert finished.wait(0.5)
        thread.join()
        peer.close()

    def test_nested_authentication_request_shares_outer_deadline(self):
        client = transport.HttpClient('http://printer')
        with patch.object(transport, 'NETWORK_TIMEOUT', .2):
            with client.operation() as outer:
                time.sleep(.03)
                with client.operation() as inner:
                    assert inner.deadline <= outer.deadline
                    assert inner.remaining() < 0.18
        client.close()

    def test_resolution_returning_after_expiry_does_not_connect(self):
        operation = transport.NetworkOperation(.02)
        def resolve(*args, **kwargs):
            time.sleep(.04)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))]
        with patch.object(transport.socket, 'getaddrinfo', resolve), \
                patch.object(transport.socket, 'socket') as create_socket:
            with pytest.raises(TimeoutError):
                operation.connect('printer', 80)
            create_socket.assert_not_called()
        operation.finish()

    def test_multiple_connect_addresses_share_deadline(self):
        operation = transport.NetworkOperation(.08)
        class FailedSocket:
            def settimeout(self, timeout):
                pass
            def connect(self, address):
                time.sleep(.05)
                raise OSError('unreachable')
            def close(self):
                pass
            def shutdown(self, direction):
                pass
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))] * 10
        with patch.object(transport.socket, 'getaddrinfo', return_value=addresses), \
                patch.object(transport.socket, 'socket', side_effect=lambda *args: FailedSocket()) as connect:
            self.assert_bounded(lambda: operation.connect('printer', 80))
            assert connect.call_count <= 2
        operation.finish()



