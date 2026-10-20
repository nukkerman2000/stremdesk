from __future__ import annotations

import unittest

from desktop_streamer.config import Settings
from desktop_streamer.hub import StreamHub
from desktop_streamer.stream import FFmpegStreamer


class StreamLifecycleTests(unittest.TestCase):
    def test_disabled_stream_does_not_start(self) -> None:
        settings = Settings(
            host="127.0.0.1",
            port=8080,
            fps=30,
            bitrate=6000,
            encoder="auto",
            max_width=1920,
            max_height=1080,
            source="desktop",
            token="",
            open_browser=False,
            ffmpeg="/missing/ffmpeg",
            stream_enabled=False,
            splash_path="/tmp/splash.png",
        )
        hub = StreamHub()
        streamer = FFmpegStreamer(settings, hub)
        streamer.start()
        status = streamer.status()
        self.assertEqual(status["state"], "disabled")
        self.assertFalse(status["stream_enabled"])
        self.assertEqual(status["splash_path"], "/tmp/splash.png")
        self.assertIsNone(status["pid"])
        self.assertIsNone(streamer._thread)
    def test_client_count_groups_connections_by_id(self) -> None:
        hub = StreamHub()
        first = hub.subscribe("192.0.2.10")
        second = hub.subscribe("192.0.2.10")
        third = hub.subscribe("192.0.2.11")
        self.assertEqual(hub.client_count, 2)
        hub.unsubscribe(first)
        self.assertEqual(hub.client_count, 2)
        hub.unsubscribe(second)
        hub.unsubscribe(third)
        self.assertEqual(hub.client_count, 0)

    def test_connections_report_transport_and_bytes(self) -> None:
        hub = StreamHub()
        subscription = hub.subscribe(
            "192.0.2.10",
            transport="vlc",
            user_agent="VLC/3.0",
            remote_port=5555,
        )
        hub.mark_sent(subscription, 2048)
        connections = hub.connections()
        self.assertEqual(len(connections), 1)
        connection = connections[0]
        self.assertEqual(connection["host"], "192.0.2.10")
        self.assertEqual(connection["transport"], "vlc")
        self.assertEqual(connection["user_agent"], "VLC/3.0")
        self.assertEqual(connection["port"], 5555)
        self.assertEqual(connection["bytes_sent"], 2048)
        self.assertGreaterEqual(connection["connected_for"], 0.0)
        self.assertGreaterEqual(connection["idle_for"], 0.0)
        hub.mark_sent(subscription, 0)
        self.assertEqual(hub.connections()[0]["bytes_sent"], 2048)
        hub.unsubscribe(subscription)
        self.assertEqual(hub.connections(), [])


if __name__ == "__main__":
    unittest.main()
