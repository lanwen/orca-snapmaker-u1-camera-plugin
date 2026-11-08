"""Typed Moonraker operations with bounded HTTP and WebSocket transport."""

# Load hostname codecs during plugin import, before audited camera callbacks.
import encodings.idna
import email.utils
from dataclasses import dataclass
import http.client
import json
import socket
import ssl
import threading
from contextlib import contextmanager
import time
from urllib.parse import quote, urlsplit

import websocket


@dataclass(frozen=True)
class ObjectQuery:
    name: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class LightCommand:
    enabled: bool


@dataclass(frozen=True)
class CameraSnapshot:
    data: bytes
    modified_at: float | None


def printer_url(value):
    value = str(value or "").strip().strip('"')
    if not value:
        raise ValueError('Printer address missing. Set "host" in plugin Config or add an address to the selected U1 preset.')
    if "://" not in value:
        value = "http://" + value
    parts = urlsplit(value)
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ("", "/") or any(c.isspace() for c in value)):
        raise ValueError("Use an HTTP or HTTPS printer address without a path or credentials.")
    parts.port  # Validate the port before starting a worker.
    return value.rstrip("/")


NETWORK_TIMEOUT = 2


class NetworkOperation:
    """An elapsed deadline that interrupts socket I/O, including buffered reads."""

    def __init__(self, timeout, deadline=None):
        self.deadline = time.monotonic() + timeout
        if deadline is not None:
            self.deadline = min(self.deadline, deadline)
        self.lock = threading.Lock()
        self.sockets = []
        self.stopped = False
        self.timer = threading.Timer(max(0, self.deadline - time.monotonic()), self.cancel)
        self.timer.daemon = True
        self.timer.start()

    def remaining(self):
        with self.lock:
            remaining = self.deadline - time.monotonic()
            if self.stopped or remaining <= 0:
                raise TimeoutError("Printer operation expired or was cancelled")
            return remaining

    def track(self, sock):
        if sock is None:
            raise OSError("Printer socket is closed")
        with self.lock:
            self.sockets.append(sock)
            stopped = self.stopped
        if stopped:
            self.interrupt(sock)
            sock.close()
        self.remaining()
        return sock

    @staticmethod
    def interrupt(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def cancel(self):
        with self.lock:
            self.stopped = True
            sockets = list(self.sockets)
        for sock in sockets:
            self.interrupt(sock)

    def finish(self):
        self.timer.cancel()
        self.timer.join()
        if self.stopped:
            for sock in self.sockets:
                sock.close()

    def connect(self, host, port, secure=False):
        # The OS resolver is synchronous and cannot be interrupted portably. Check
        # the deadline immediately after it returns; never open a late connection.
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        self.remaining()
        error = OSError("Printer address did not resolve")
        for family, kind, protocol, _, address in addresses:
            sock = self.track(socket.socket(family, kind, protocol))
            try:
                sock.settimeout(self.remaining())
                sock.connect(address)
                if secure:
                    # Register the SSLSocket before the handshake. wrap_socket
                    # transfers ownership of the raw descriptor to this socket.
                    sock = self.track(ssl.create_default_context().wrap_socket(
                        sock, server_hostname=host, do_handshake_on_connect=False))
                    sock.settimeout(self.remaining())
                    sock.do_handshake()
                self.remaining()
                return sock
            except OSError as failure:
                sock.close()
                error = failure
                self.remaining()
        raise error


class HttpClient:
    def __init__(self, address, api_key=""):
        self.address = printer_url(address)
        self.parts = urlsplit(self.address)
        self.api_key = api_key
        self.ws = None
        self.request_id = 0
        self.operation_lock = threading.Lock()
        self.operations = set()
        self.closed = False

    @contextmanager
    def operation(self):
        with self.operation_lock:
            deadline = min((active.deadline for active in self.operations), default=None)
            operation = NetworkOperation(NETWORK_TIMEOUT, deadline)
            if self.closed:
                operation.cancel()
            self.operations.add(operation)
        try:
            operation.remaining()
            yield operation
            operation.remaining()
        except Exception:
            operation.remaining()
            raise
        finally:
            operation.finish()
            with self.operation_lock:
                self.operations.discard(operation)
            self.close_websocket_if_idle()

    def start_monitor(self) -> None:
        with self.operation() as operation:
            if self.ws is None:
                url = self.websocket_url()
                sock = operation.connect(self.parts.hostname,
                                         self.parts.port or (443 if self.parts.scheme == "https" else 80),
                                         self.parts.scheme == "https")
                try:
                    self.ws = websocket.create_connection(
                        url, socket=sock, timeout=operation.remaining(),
                        suppress_origin=True, http_no_proxy=["*"], redirect_limit=0,
                    )
                except BaseException:
                    sock.close()
                    raise
            operation.track(self.ws.sock)
            self.request_id += 1
            request_id = self.request_id
            self.ws.settimeout(operation.remaining())
            self.ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                     "method": "camera.start_monitor",
                                     "params": {"domain": "lan", "interval": 0}}))
            while True:
                self.ws.settimeout(operation.remaining())
                raw = self.ws.recv()
                operation.remaining()
                if len(raw) > 512_000:
                    raise RuntimeError("Camera response is too large.")
                reply = json.loads(raw)
                if reply.get("id") == request_id:
                    if "error" in reply:
                        raise RuntimeError("Printer rejected camera monitoring.")
                    return

    def close(self) -> None:
        with self.operation_lock:
            self.closed = True
            operations = list(self.operations)
        for operation in operations:
            operation.cancel()
        self.close_websocket_if_idle()

    def close_websocket_if_idle(self):
        # Keep descriptors open until recv leaves its selector. Closing one from
        # another thread can silently remove its event on macOS and strand recv.
        with self.operation_lock:
            ws = self.ws if self.closed and not self.operations else None
            if ws is not None:
                self.ws = None
        if ws is not None:
            ws.close(timeout=0)

    def query_objects(self, *objects: ObjectQuery) -> dict:
        query = "&".join(quote(obj.name, safe="") +
                         ("=" + quote(",".join(obj.fields), safe=",") if obj.fields else "")
                         for obj in objects)
        return self._query("/printer/objects/query?" + query)["status"]

    def file_metadata(self, filename: str) -> dict:
        return self._query("/server/files/metadata?filename=" + quote(filename, safe=""))

    def set_light(self, command: LightCommand) -> None:
        if type(command.enabled) is not bool:
            raise ValueError("Light state must be on or off.")
        self._query("/printer/gcode/script", {
            "script": "SET_LED LED=cavity_led WHITE=%d SYNC=0" % command.enabled,
        })

    def camera_snapshot(self) -> CameraSnapshot:
        data, modified = self.request("/server/files/camera/monitor.jpg?cacheBust=" + str(time.time_ns()),
                                      limit=4_000_000)
        if not data.startswith(b"\xff\xd8"):
            raise RuntimeError("The camera did not return a JPEG image.")
        modified_at = None
        if modified:
            try:
                modified_at = email.utils.parsedate_to_datetime(modified).timestamp()
            except (TypeError, ValueError, OverflowError):
                raise RuntimeError("Camera returned an invalid image timestamp.")
        return CameraSnapshot(data, modified_at)

    def websocket_url(self) -> str:
        suffix = ""
        if self.api_key:
            token = self._query("/access/oneshot_token")
            suffix = "?token=" + quote(token, safe="")
        scheme = "wss" if self.parts.scheme == "https" else "ws"
        return scheme + "://" + self.parts.netloc + "/websocket" + suffix

    def _query(self, path, payload=None):
        result = self.json(path, payload)
        if "error" in result:
            raise RuntimeError("Printer rejected the request.")
        return result["result"]

    def request(self, path, payload=None, limit=512_000):
        connection_type = (http.client.HTTPSConnection if self.parts.scheme == "https"
                           else http.client.HTTPConnection)
        conn = connection_type(self.parts.hostname, self.parts.port, timeout=2)
        headers = {"Cache-Control": "no-cache"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        try:
            with self.operation() as operation:
                def connect():
                    conn.sock = operation.connect(self.parts.hostname,
                                                  self.parts.port or conn.default_port,
                                                  self.parts.scheme == "https")
                conn.connect = connect
                conn.request("POST" if body is not None else "GET", path, body, headers)
                response = conn.getresponse()
                try:
                    if response.status in (401, 403):
                        raise RuntimeError("Printer denied access. Check its API key and LAN permissions.")
                    if response.status != 200:
                        raise RuntimeError("Printer returned HTTP %s" % response.status)
                    data = response.read(limit + 1)
                    operation.remaining()
                    if len(data) > limit:
                        raise RuntimeError("Printer response is too large.")
                    return data, response.getheader("Last-Modified")
                finally:
                    response.close()
        finally:
            conn.close()

    def json(self, path, payload=None):
        data, _ = self.request(path, payload)
        return json.loads(data)
