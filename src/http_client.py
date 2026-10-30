"""Typed Moonraker operations with bounded HTTP and WebSocket transport."""

# Load hostname codecs during plugin import, before audited camera callbacks.
import encodings.idna
import email.utils
from dataclasses import dataclass
import http.client
import json
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
        raise ValueError('Set the selected printer address, or set "host" in plugin Config.')
    if "://" not in value:
        value = "http://" + value
    parts = urlsplit(value)
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ("", "/") or any(c.isspace() for c in value)):
        raise ValueError("Use an HTTP or HTTPS printer address without a path or credentials.")
    parts.port  # Validate the port before starting a worker.
    return value.rstrip("/")


class HttpClient:
    def __init__(self, address, api_key=""):
        self.address = printer_url(address)
        self.parts = urlsplit(self.address)
        self.api_key = api_key
        self.ws = None
        self.request_id = 0

    def start_monitor(self) -> None:
        if self.ws is None:
            self.ws = websocket.create_connection(
                self.websocket_url(),
                timeout=2, suppress_origin=True, http_no_proxy=["*"],
            )
        self.request_id += 1
        request_id = self.request_id
        self.ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                 "method": "camera.start_monitor",
                                 "params": {"domain": "lan", "interval": 0}}))
        deadline = time.monotonic() + 2
        for _ in range(100):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.ws.settimeout(remaining)
            raw = self.ws.recv()
            if len(raw) > 512_000:
                raise RuntimeError("Camera response is too large.")
            reply = json.loads(raw)
            if reply.get("id") == request_id:
                if "error" in reply:
                    raise RuntimeError("Printer rejected camera monitoring.")
                return
        raise RuntimeError("Camera did not acknowledge monitoring.")

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close(timeout=0)
            self.ws = None

    def query_objects(self, *objects: ObjectQuery) -> dict:
        query = "&".join(quote(obj.name, safe="") +
                         ("=" + quote(",".join(obj.fields), safe=",") if obj.fields else "")
                         for obj in objects)
        return self._query("/printer/objects/query?" + query)["status"]

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
            conn.request("POST" if body is not None else "GET", path, body, headers)
            response = conn.getresponse()
            if response.status in (401, 403):
                raise RuntimeError("Printer denied access. Check its API key and LAN permissions.")
            if response.status != 200:
                raise RuntimeError("Printer returned HTTP %s." % response.status)
            data = response.read(limit + 1)
            if len(data) > limit:
                raise RuntimeError("Printer response is too large.")
            return data, response.getheader("Last-Modified")
        finally:
            conn.close()

    def json(self, path, payload=None):
        data, _ = self.request(path, payload)
        return json.loads(data)
