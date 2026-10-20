from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import select
import socket
import struct
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

from .config import Settings
from .hub import StreamHub, Subscription
from .stream import FFmpegStreamer


LOGGER = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).with_name("web")
SPLASH_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
SPLASH_MAX_BYTES = 10 * 1024 * 1024


@dataclass(slots=True)
class ServerContext:
    settings: Settings
    hub: StreamHub
    streamer: FFmpegStreamer
    port: int


class DesktopHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, address: tuple[str, int], context: ServerContext) -> None:
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.context = context
        super().__init__(address, DesktopRequestHandler)

    def handle_error(self, request: object, client_address: object) -> None:
        LOGGER.exception("Ошибка HTTP-соединения")


class DesktopRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/healthz":
            self._send_json(HTTPStatus.OK, {"ok": True, "stream": self._context().streamer.status()["state"]})
            return
        if path in {"/", "/index.html"}:
            if not self._authorized(parsed.query):
                self._send_text(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            self._send_file("index.html", "text/html; charset=utf-8")
            return
        if path in {"/app.js", "/style.css", "/placeholder.svg"}:
            content_types = {
                "/app.js": "text/javascript; charset=utf-8",
                "/style.css": "text/css; charset=utf-8",
                "/placeholder.svg": "image/svg+xml",
            }
            self._send_file(path[1:], content_types[path])
            return
        if path == "/splash":
            if not self._authorized(parsed.query):
                self._send_text(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            self._send_splash()
            return
        if path == "/api/status":
            if not self._authorized(parsed.query):
                self._send_text(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            self._send_json(HTTPStatus.OK, self._status_payload())
            return
        if path in {"/vlc.mp4", "/vlc", "/stream.mp4"}:
            if not self._authorized(parsed.query):
                self._send_text(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            self._serve_vlc()
            return
        if path in {"/ws", "/stream"}:
            self._serve_websocket(parsed.query)
            return
        if path == "/hls/index.m3u8" or path.startswith("/hls/"):
            if not self._authorized(parsed.query):
                self._send_text(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            self._serve_hls(path)
            return
        self._send_text(HTTPStatus.NOT_FOUND, "Not found")

    def do_HEAD(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path in {"/healthz", "/api/status"}:
            self._send_json(HTTPStatus.OK, {"ok": True})
        elif path == "/splash":
            if not self._authorized(parsed.query):
                self._send_text(HTTPStatus.UNAUTHORIZED, "Unauthorized")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send_text(HTTPStatus.NOT_FOUND, "Not found")

    def _context(self) -> ServerContext:
        return cast(DesktopHTTPServer, self.server).context

    def _token_from_query(self, query: str) -> str:
        values = parse_qs(query).get("token", [])
        return values[0] if values else ""

    def _authorized(self, query: str) -> bool:
        expected = self._context().settings.token
        if not expected:
            return True
        supplied = self._token_from_query(query)
        authorization = self.headers.get("Authorization", "")
        if not supplied and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        return hmac.compare_digest(supplied, expected)

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin or not self._context().settings.token:
            return True
        parsed = urlsplit(origin)
        hostname = (parsed.hostname or "").lower()
        return hostname in {"localhost", "127.0.0.1", "::1"}

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if headers:
            for name, value in headers.items():
                self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)
            self.wfile.flush()

    def _send_text(self, status: HTTPStatus, text: str) -> None:
        self._send_bytes(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_file(self, name: str, content_type: str) -> None:
        path = WEB_ROOT / name
        try:
            body = path.read_bytes()
        except OSError:
            self._send_text(HTTPStatus.NOT_FOUND, "Not found")
            return
        self._send_bytes(HTTPStatus.OK, body, content_type)

    def _send_splash(self) -> None:
        configured = self._context().settings.splash_path
        if configured:
            try:
                path = Path(configured).expanduser()
                content_type = SPLASH_TYPES.get(path.suffix.lower())
                if content_type and path.is_file() and path.stat().st_size <= SPLASH_MAX_BYTES:
                    self._send_bytes(HTTPStatus.OK, path.read_bytes(), content_type)
                    return
            except OSError:
                LOGGER.warning("Не удалось прочитать заставку: %s", configured)
        self._send_file("placeholder.svg", "image/svg+xml")

    def _status_payload(self) -> dict:
        context = self._context()
        status = context.streamer.status()
        connections = context.hub.connections()
        status.update(
            {
                "stream_enabled": context.settings.stream_enabled,
                "splash_path": context.settings.splash_path,
                "ui_language": context.settings.ui_language,
                "connection_count": len(connections),
                "connections": connections,
                "server_pid": os.getpid(),
            }
        )
        host = context.settings.host
        browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        if ":" in browser_host and not browser_host.startswith("["):
            browser_host = f"[{browser_host}]"
        base = f"http://{browser_host}:{context.port}"
        status["browser_url"] = base + "/"
        status["vlc_url"] = base + "/vlc.mp4"
        status["hls_url"] = base + "/hls/index.m3u8"
        return status

    def _subscribe(self, transport: str) -> Subscription:
        return self._context().hub.subscribe(
            self.client_address[0],
            transport=transport,
            user_agent=self.headers.get("User-Agent", ""),
            remote_port=self.client_address[1],
        )

    def _serve_vlc(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.close_connection = True
        hub = self._context().hub
        subscription = self._subscribe("vlc")
        self.connection.settimeout(15)
        try:
            while True:
                if self._peer_closed():
                    break
                chunk = subscription.get(1)
                if chunk is None:
                    if self._peer_closed():
                        break
                    continue
                if chunk.kind == "reset":
                    break
                if chunk.data:
                    self.wfile.write(chunk.data)
                    self.wfile.flush()
                    hub.mark_sent(subscription, len(chunk.data))
        except (BrokenPipeError, ConnectionResetError, OSError, TimeoutError):
            pass
        finally:
            hub.unsubscribe(subscription)

    def _peer_closed(self) -> bool:
        while select.select([self.connection], [], [], 0)[0]:
            try:
                data = self.connection.recv(4096)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                return True
            if not data:
                return True
        return False

    def _serve_hls(self, path: str) -> None:
        """Отдаёт плейлист и сегменты HLS для iOS/Safari (нативная поддержка)."""
        directory = self._context().streamer.hls_dir
        if directory is None or not directory.is_dir():
            self._send_text(HTTPStatus.NOT_FOUND, "Not found")
            return
        name = path[len("/hls/") :]
        if not name:
            self._send_text(HTTPStatus.NOT_FOUND, "Not found")
            return
        target = (directory / name).resolve()
        try:
            target.relative_to(directory.resolve())
        except ValueError:
            self._send_text(HTTPStatus.NOT_FOUND, "Not found")
            return
        if not target.is_file():
            self._send_text(HTTPStatus.NOT_FOUND, "Not found")
            return
        if target.suffix == ".m3u8":
            content_type = "application/vnd.apple.mpegurl"
        elif target.suffix == ".ts":
            content_type = "video/mp2t"
        elif target.suffix == ".mp4":
            content_type = "video/mp4"
        else:
            self._send_text(HTTPStatus.NOT_FOUND, "Not found")
            return
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if target.suffix == ".m3u8":
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        else:
            self.send_header("Cache-Control", "max-age=10")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _serve_websocket(self, query: str) -> None:
        if not self._authorized(query):
            self._send_text(HTTPStatus.UNAUTHORIZED, "Unauthorized")
            return
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self._send_text(HTTPStatus.BAD_REQUEST, "WebSocket upgrade required")
            return
        if not self._origin_allowed():
            self._send_text(HTTPStatus.FORBIDDEN, "Origin is not allowed")
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self._send_text(HTTPStatus.BAD_REQUEST, "Sec-WebSocket-Key required")
            return
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        self.connection.settimeout(15)
        hub = self._context().hub
        subscription = self._subscribe("websocket")
        input_buffer = bytearray()
        try:
            while True:
                if self._consume_websocket_input(input_buffer):
                    break
                chunk = subscription.get(1)
                if chunk is None:
                    if self._consume_websocket_input(input_buffer):
                        break
                    self._websocket_frame(0x89, b"")
                    continue
                if self._consume_websocket_input(input_buffer):
                    break
                if chunk.kind == "reset":
                    self._websocket_frame(0x88, struct.pack("!H", 1012))
                    break
                if chunk.data:
                    self._websocket_frame(0x2, chunk.data)
                    hub.mark_sent(subscription, len(chunk.data))
        except (BrokenPipeError, ConnectionResetError, OSError, TimeoutError):
            pass
        finally:
            hub.unsubscribe(subscription)

    def _consume_websocket_input(self, buffer: bytearray) -> bool:
        while select.select([self.connection], [], [], 0)[0]:
            try:
                data = self.connection.recv(4096)
            except (BlockingIOError, InterruptedError):
                continue
            if not data:
                return True
            buffer.extend(data)
        while len(buffer) >= 2:
            first = buffer[0]
            second = buffer[1]
            length = second & 0x7F
            header_length = 2
            if length == 126:
                if len(buffer) < 4:
                    break
                length = int.from_bytes(buffer[2:4], "big")
                header_length = 4
            elif length == 127:
                if len(buffer) < 10:
                    break
                length = int.from_bytes(buffer[2:10], "big")
                header_length = 10
            masked = bool(second & 0x80)
            payload_start = header_length + 4 if masked else header_length
            if len(buffer) < payload_start + length:
                break
            mask = bytes(buffer[header_length:payload_start]) if masked else b""
            payload = bytes(buffer[payload_start:payload_start + length])
            del buffer[:payload_start + length]
            opcode = first & 0x0F
            if opcode == 0x08:
                return True
            if opcode == 0x09:
                if masked:
                    payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
                self._websocket_frame(0x0A, payload)
        return False

    def _websocket_frame(self, opcode: int, payload: bytes) -> None:
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, length)
        elif length < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
        self.connection.sendall(header + payload)
