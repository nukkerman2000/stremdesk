from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from desktop_streamer.config import Settings, parse_settings
from desktop_streamer.stream import build_ffmpeg_command, build_hls_command
from stremdesk_settings import _server_arguments


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{}", encoding="utf-8")
            settings = parse_settings(["--config", str(path)])
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8080)
        self.assertEqual(settings.encoder, "auto")
        self.assertEqual(settings.source, "auto")

    def test_gui_server_command_uses_entrypoint(self) -> None:
        arguments = _server_arguments(["--config", "relative.json", "--no-browser"], Path("/tmp/stremdesk-config.json"))
        self.assertTrue(arguments[0].endswith("/stremdesk.py"))
        self.assertEqual(arguments[1:], ["--config", "/tmp/stremdesk-config.json", "--no-browser"])

    def test_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            parse_settings(["--fps", "0"])
        with self.assertRaises(ValueError):
            parse_settings(["--port", "65536"])
        with self.assertRaises(ValueError):
            parse_settings(["--token", "short"])

    def test_extended_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{}", encoding="utf-8")
            settings = parse_settings(["--config", str(path)])
        self.assertFalse(settings.audio_enabled)
        self.assertEqual(settings.audio_source, "")
        self.assertEqual(settings.audio_bitrate, 128)
        self.assertEqual(settings.capture_mode, "screen")
        self.assertEqual(settings.video_codec, "h264")
        self.assertTrue(settings.server_enabled)
        self.assertTrue(settings.stream_enabled)
        self.assertEqual(settings.splash_path, "")
        self.assertEqual(settings.ui_language, "ru")

    def test_extended_file_and_cli_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "capture_mode": "camera",
                        "camera_device": "/dev/video2",
                        "audio_enabled": True,
                        "audio_source": "monitor",
                        "video_codec": "hevc",
                        "server_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            settings = parse_settings(["--config", str(path)])
            self.assertEqual(settings.capture_mode, "camera")
            self.assertEqual(settings.camera_device, "/dev/video2")
            self.assertTrue(settings.audio_enabled)
            self.assertEqual(settings.audio_source, "monitor")
            self.assertEqual(settings.video_codec, "hevc")
            self.assertFalse(settings.server_enabled)

    def test_stream_and_language_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            splash = Path(directory) / "splash.png"
            splash.write_bytes(b"png")
            settings = parse_settings(
                [
                    "--no-stream-enabled",
                    "--splash-path",
                    str(splash),
                    "--ui-language",
                    "uk",
                ]
            )
        self.assertFalse(settings.stream_enabled)
        self.assertEqual(settings.splash_path, str(splash))
        self.assertEqual(settings.ui_language, "uk")

    def test_invalid_stream_metadata(self) -> None:
        with self.assertRaises(ValueError):
            parse_settings(["--splash-path", "relative.png"])
        with self.assertRaises(SystemExit):
            parse_settings(["--ui-language", "pl"])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"port": 9123, "fps": 24, "encoder": "x264"}), encoding="utf-8")
            settings = parse_settings(["--config", str(path)])
            self.assertEqual(settings.port, 9123)
            self.assertEqual(settings.fps, 24)
            self.assertEqual(settings.encoder, "x264")
            overridden = parse_settings(["--config", str(path), "--fps", "60"])
            self.assertEqual(overridden.fps, 60)


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
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
            ffmpeg=None,
        )

    def test_contains_low_latency_outputs(self) -> None:
        command = build_ffmpeg_command("ffmpeg.exe", self.settings, "nvenc", "gdigrab")
        self.assertIn("h264_nvenc", command)
        self.assertTrue(any("empty_moov" in item for item in command))
        self.assertTrue(any("default_base_moof" in item for item in command))
        self.assertIn("pipe:1", command)

    def test_linux_x11grab_command(self) -> None:
        command = build_ffmpeg_command("ffmpeg", self.settings, "nvenc", "x11grab")
        self.assertIn("x11grab", command)
        self.assertIn("-draw_mouse", command)
        self.assertIn("h264_nvenc", command)
        self.assertNotIn("gdigrab", command)

    def test_camera_audio_command(self) -> None:
        settings = parse_settings(
            [
                "--capture-mode",
                "camera",
                "--camera-device",
                "/dev/video0",
                "--audio-enabled",
                "--audio-source",
                "default",
            ]
        )
        command = build_ffmpeg_command("ffmpeg", settings, "nvenc", "v4l2")
        self.assertIn("v4l2", command)
        self.assertIn("/dev/video0", command)
        self.assertIn("pulse", command)
        self.assertIn("aac", command)
        self.assertNotIn("-an", command)

    def test_hevc_window_command(self) -> None:
        settings = parse_settings(
            ["--capture-mode", "window", "--window-id", "12345", "--video-codec", "hevc"]
        )
        command = build_ffmpeg_command("ffmpeg", settings, "nvenc", "x11grab")
        self.assertIn("-window_id", command)
        self.assertIn("12345", command)
        self.assertIn("hevc_nvenc", command)
        self.assertNotIn("h264_nvenc", command)

    def test_x264_fallback(self) -> None:
        command = build_ffmpeg_command("ffmpeg.exe", self.settings, "x264")
        self.assertIn("libx264", command)
        self.assertIn("zerolatency", command)

    def test_voice_mode_uses_black_frame_without_splash(self) -> None:
        settings = parse_settings(["--capture-mode", "voice", "--audio-enabled", "--audio-source", "default"])
        self.assertEqual(settings.capture_mode, "voice")
        command = build_ffmpeg_command("ffmpeg", settings, "nvenc", "x11grab")
        self.assertIn("lavfi", command)
        self.assertIn("color=c=black", " ".join(command))
        self.assertNotIn("x11grab", command)
        self.assertNotIn("v4l2", command)
        self.assertIn("pulse", command)
        self.assertIn("aac", command)

    def test_voice_mode_uses_splash_image_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "splash.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            settings = parse_settings(
                [
                    "--capture-mode",
                    "voice",
                    "--audio-enabled",
                    "--audio-source",
                    "default",
                    "--splash-path",
                    str(image),
                ]
            )
            command = build_ffmpeg_command("ffmpeg", settings, "nvenc", "x11grab")
        joined = " ".join(command)
        self.assertIn("-loop", command)
        self.assertIn(str(image.resolve()), joined)
        self.assertNotIn("lavfi", command)
        self.assertNotIn("color=c=black", joined)

    def test_voice_mode_ignores_missing_splash_file(self) -> None:
        settings = parse_settings(["--capture-mode", "voice", "--splash-path", "/nonexistent/splash.png"])
        command = build_ffmpeg_command("ffmpeg", settings, "nvenc", "x11grab")
        self.assertIn("color=c=black", " ".join(command))
        self.assertNotIn("-loop", command)

    def test_voice_mode_caps_frame_rate(self) -> None:
        settings = parse_settings(["--capture-mode", "voice", "--fps", "60"])
        command = build_ffmpeg_command("ffmpeg", settings, "nvenc", "x11grab")
        filters = command[command.index("-vf") + 1]
        self.assertTrue(filters.startswith("fps=5,"), filters)
        self.assertIn("5", command[command.index("-framerate") + 1])


    def test_hls_command_remuxes_from_stdin_without_capture(self) -> None:
        command = build_hls_command("ffmpeg", "/tmp/hls-dir")
        self.assertIn("pipe:0", command)
        self.assertNotIn("pipe:1", command)
        # камера/экран не открывается вторым процессом — только переупаковка
        for capture in ("v4l2", "x11grab", "/dev/video0", "lavfi", "PulseAudio", "alsa"):
            self.assertNotIn(capture, command)
        self.assertEqual(command[command.index("-c") + 1], "copy")
        self.assertEqual(command[command.index("-hls_time") + 1], "1")
        self.assertEqual(command[command.index("-hls_segment_type") + 1], "mpegts")
        self.assertTrue(command[-1].endswith("index.m3u8"))
        self.assertIn("delete_segments", command[command.index("-hls_flags") + 1])
        self.assertIn("omit_endlist", command[command.index("-hls_flags") + 1])

    def test_main_command_has_no_hls_muxer(self) -> None:
        command = build_ffmpeg_command("ffmpeg", self.settings, "x264")
        self.assertEqual(command[-3:], ["-f", "mp4", "pipe:1"])
        self.assertNotIn("index.m3u8", " ".join(command))


if __name__ == "__main__":
    unittest.main()
