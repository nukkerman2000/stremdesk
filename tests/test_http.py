from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from desktop_streamer.config import Settings
from desktop_streamer.hub import StreamHub
from desktop_streamer.http_server import DesktopHTTPServer, ServerContext
from desktop_streamer.stream import FFmpegStreamer


class HttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            host="127.0.0.1",
            port=0,
            fps=30,
            bitrate=6000,
            encoder="x264",
            max_width=0,
            max_height=0,
            source="desktop",
            token="test-token",
            open_browser=False,
            ffmpeg="ffmpeg.exe",
        )
        self.context = ServerContext(
            settings=self.settings,
            hub=StreamHub(),
            streamer=FFmpegStreamer(self.settings, StreamHub()),
            port=0,
        )
        self.server = DesktopHTTPServer((self.settings.host, 0), self.context)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_health_is_public(self) -> None:
        with urlopen(f"{self.base}/healthz", timeout=2) as response:
            payload = json.load(response)
        self.assertTrue(payload["ok"])

    def test_status_requires_token(self) -> None:
        with self.assertRaises(HTTPError) as error:
            urlopen(f"{self.base}/api/status", timeout=2)
        self.assertEqual(error.exception.code, 401)
        request = Request(f"{self.base}/api/status?token=test-token")
        with urlopen(request, timeout=2) as response:
            payload = json.load(response)
        self.assertEqual(payload["state"], "idle")
    def test_hls_requires_token_and_serves_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.m3u8").write_text("#EXTM3U\n#EXTINF:1.0,\nseg_000000.ts\n", encoding="utf-8")
            (root / "seg_000000.ts").write_bytes(b"\x47" + b"\x00" * 99)
            self.context.streamer._hls_dir = root
            with self.assertRaises(HTTPError) as error:
                urlopen(f"{self.base}/hls/index.m3u8", timeout=2)
            self.assertEqual(error.exception.code, 401)
            request = Request(f"{self.base}/hls/index.m3u8?token=test-token")
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.headers.get_content_type(), "application/vnd.apple.mpegurl")
                self.assertIn("#EXTM3U", response.read().decode())
            request = Request(f"{self.base}/hls/seg_000000.ts?token=test-token")
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.headers.get_content_type(), "video/mp2t")
            self.context.streamer._hls_dir = None

    def test_hls_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.context.streamer._hls_dir = Path(directory)
            for attempt in ("/hls/../config.json", "/hls/%2e%2e/config.json"):
                with self.assertRaises(HTTPError) as error:
                    Request(f"{self.base}{attempt}?token=test-token")
                    urlopen(f"{self.base}{attempt}?token=test-token", timeout=2)
                self.assertEqual(error.exception.code, 404)
            self.context.streamer._hls_dir = None

    def test_status_reports_server_pid(self) -> None:
        request = Request(f"{self.base}/api/status?token=test-token")
        with urlopen(request, timeout=2) as response:
            payload = json.load(response)
        self.assertEqual(payload["server_pid"], os.getpid())

    def test_splash_requires_token_and_serves_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "splash.png"
            path.write_bytes(b"custom-splash")
            self.context.settings = replace(self.settings, splash_path=str(path))
            with self.assertRaises(HTTPError) as error:
                urlopen(f"{self.base}/splash", timeout=2)
            self.assertEqual(error.exception.code, 401)
            request = Request(f"{self.base}/splash?token=test-token")
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.headers.get_content_type(), "image/png")
                self.assertEqual(response.read(), b"custom-splash")

    def test_splash_falls_back_to_placeholder(self) -> None:
        self.context.settings = replace(self.settings, splash_path="/missing/splash.png")
        request = Request(f"{self.base}/splash?token=test-token")
        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), "image/svg+xml")
            self.assertIn(b"<svg", response.read())

        settings = replace(self.settings, token="")
        hub = StreamHub()
        context = ServerContext(
            settings=settings,
            hub=hub,
            streamer=FFmpegStreamer(settings, hub),
            port=0,
        )
        server = DesktopHTTPServer((settings.host, 0), context)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with urlopen(f"{base}/api/status", timeout=2) as response:
                payload = json.load(response)
            self.assertEqual(payload["state"], "idle")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_status_exposes_connections(self) -> None:
        subscription = self.context.hub.subscribe(
            "192.0.2.20",
            transport="websocket",
            user_agent="Probe/1.0",
            remote_port=4321,
        )
        self.context.hub.mark_sent(subscription, 512)
        try:
            request = Request(f"{self.base}/api/status?token=test-token")
            with urlopen(request, timeout=2) as response:
                payload = json.load(response)
            self.assertEqual(payload["connection_count"], 1)
            self.assertEqual(payload["connections"][0]["host"], "192.0.2.20")
            self.assertEqual(payload["connections"][0]["transport"], "websocket")
            self.assertEqual(payload["connections"][0]["user_agent"], "Probe/1.0")
            self.assertEqual(payload["connections"][0]["bytes_sent"], 512)
        finally:
            self.context.hub.unsubscribe(subscription)

    def test_closed_vlc_client_is_removed(self) -> None:
        connection = socket.create_connection(("127.0.0.1", self.server.server_address[1]), timeout=2)
        try:
            connection.sendall(
                b"GET /vlc.mp4?token=test-token HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"User-Agent: VLC/3.0\r\n"
                b"\r\n"
            )
            self._wait_for_client_count(1)
        finally:
            connection.close()
        self._wait_for_client_count(0)

    def _wait_for_client_count(self, expected: int) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.context.hub.client_count != expected:
            time.sleep(0.05)
        self.assertEqual(self.context.hub.client_count, expected)


if __name__ == "__main__":
    unittest.main()
