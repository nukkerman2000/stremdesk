from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENCODER_NAMES = ("auto", "nvenc", "qsv", "amf", "x264", "x265")
VIDEO_CODEC_NAMES = ("h264", "hevc")
CAPTURE_MODES = ("screen", "window", "camera", "voice")
UI_LANGUAGES = ("ru", "uk", "en", "be")
SPLASH_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
VOICE_FPS = 5
VOICE_FALLBACK_SIZE = (640, 480)
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{8,128}$")
CONFIG_FIELDS = {
    "host",
    "port",
    "fps",
    "bitrate",
    "encoder",
    "max_width",
    "max_height",
    "source",
    "token",
    "open_browser",
    "ffmpeg",
    "audio_enabled",
    "audio_source",
    "audio_bitrate",
    "audio_sample_rate",
    "audio_channels",
    "capture_mode",
    "window_id",
    "camera_device",
    "video_codec",
    "draw_mouse",
    "server_enabled",
    "stream_enabled",
    "splash_path",
    "ui_language",
}


@dataclass(frozen=True, slots=True)
class Settings:
    host: str
    port: int
    fps: int
    bitrate: int
    encoder: str
    max_width: int
    max_height: int
    source: str
    token: str
    open_browser: bool
    ffmpeg: str | None
    audio_enabled: bool = False
    audio_source: str = ""
    audio_bitrate: int = 128
    audio_sample_rate: int = 48000
    audio_channels: int = 2
    capture_mode: str = "screen"
    window_id: str = ""
    camera_device: str = ""
    video_codec: str = "h264"
    draw_mouse: bool = True
    server_enabled: bool = True
    stream_enabled: bool = True
    splash_path: str = ""
    ui_language: str = "ru"

    @property
    def loopback(self) -> bool:
        host = self.host.lower().strip("[]")
        return host in {"localhost", "::1"} or host.startswith("127.")


def config_path_from_args(arguments: list[str]) -> Path:
    configured = os.environ.get("STREMDESK_CONFIG")
    if configured:
        return Path(configured).expanduser()
    for index, argument in enumerate(arguments):
        if argument == "--config" and index + 1 < len(arguments):
            return Path(arguments[index + 1]).expanduser()
        if argument.startswith("--config="):
            return Path(argument.split("=", 1)[1]).expanduser()
    return Path(__file__).resolve().parents[1] / "config.json"


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"не удалось прочитать конфиг {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"некорректный JSON в {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"конфиг {path} должен содержать JSON-объект")
    unknown = set(value) - CONFIG_FIELDS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"неизвестные параметры в {path}: {names}")
    return value


def _default_values() -> dict[str, Any]:
    return {
        "host": os.environ.get("STREMDESK_HOST", "127.0.0.1"),
        "port": int(os.environ.get("STREMDESK_PORT", "8080")),
        "fps": int(os.environ.get("STREMDESK_FPS", "30")),
        "bitrate": int(os.environ.get("STREMDESK_BITRATE", "6000")),
        "encoder": os.environ.get("STREMDESK_ENCODER", "auto"),
        "max_width": int(os.environ.get("STREMDESK_MAX_WIDTH", "1920")),
        "max_height": int(os.environ.get("STREMDESK_MAX_HEIGHT", "1080")),
        "source": os.environ.get("STREMDESK_SOURCE", "auto"),
        "token": os.environ.get("STREMDESK_TOKEN", ""),
        "open_browser": True,
        "ffmpeg": os.environ.get("STREMDESK_FFMPEG", ""),
        "audio_enabled": os.environ.get("STREMDESK_AUDIO_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        "audio_source": os.environ.get("STREMDESK_AUDIO_SOURCE", ""),
        "audio_bitrate": int(os.environ.get("STREMDESK_AUDIO_BITRATE", "128")),
        "audio_sample_rate": int(os.environ.get("STREMDESK_AUDIO_SAMPLE_RATE", "48000")),
        "audio_channels": int(os.environ.get("STREMDESK_AUDIO_CHANNELS", "2")),
        "capture_mode": os.environ.get("STREMDESK_CAPTURE_MODE", "screen"),
        "window_id": os.environ.get("STREMDESK_WINDOW_ID", ""),
        "camera_device": os.environ.get("STREMDESK_CAMERA_DEVICE", ""),
        "video_codec": os.environ.get("STREMDESK_VIDEO_CODEC", "h264"),
        "draw_mouse": os.environ.get("STREMDESK_DRAW_MOUSE", "true").lower() in {"1", "true", "yes", "on"},
        "server_enabled": os.environ.get("STREMDESK_SERVER_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        "stream_enabled": os.environ.get("STREMDESK_STREAM_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        "splash_path": os.environ.get("STREMDESK_SPLASH_PATH", ""),
        "ui_language": os.environ.get("STREMDESK_UI_LANGUAGE", "ru"),
    }


def parse_settings(argv: list[str] | None = None) -> Settings:
    arguments = list(sys.argv[1:] if argv is None else argv)
    config_path = config_path_from_args(arguments)
    values = _default_values()
    values.update(load_config(config_path))

    parser = argparse.ArgumentParser(
        description="Портативный стриминг рабочего стола через браузер и VLC"
    )
    parser.add_argument("--config", default=str(config_path), help="путь к JSON-конфигурации")
    parser.add_argument("--host", default=values["host"], help="интерфейс прослушивания")
    parser.add_argument("--port", type=int, default=values["port"], help="TCP-порт, 0 выбирает свободный")
    parser.add_argument("--fps", type=int, default=values["fps"], help="частота кадров, от 1 до 120")
    parser.add_argument("--bitrate", type=int, default=values["bitrate"], help="целевой битрейт в кбит/с")
    parser.add_argument(
        "--encoder",
        choices=ENCODER_NAMES,
        default=values["encoder"],
        help="кодировщик или auto",
    )
    parser.add_argument("--max-width", type=int, default=values["max_width"], help="максимальная ширина, 0 без ограничения")
    parser.add_argument("--max-height", type=int, default=values["max_height"], help="максимальная высота, 0 без ограничения")
    parser.add_argument(
        "--source",
        default=values["source"],
        help="источник захвата: auto/:0.0 для X11, либо имя устройства FFmpeg",
    )
    parser.add_argument(
        "--capture-mode",
        choices=CAPTURE_MODES,
        default=values["capture_mode"],
        help="режим захвата: экран, окно или камера",
    )
    parser.add_argument("--window-id", default=values["window_id"], help="X11 window ID для захвата окна")
    parser.add_argument("--camera-device", default=values["camera_device"], help="устройство камеры, например /dev/video0")
    parser.add_argument(
        "--video-codec",
        choices=VIDEO_CODEC_NAMES,
        default=values["video_codec"],
        help="видеокодек: h264 или hevc",
    )
    parser.add_argument(
        "--draw-mouse",
        action=argparse.BooleanOptionalAction,
        default=values["draw_mouse"],
        help="показывать курсор мыши в X11-захвате",
    )
    parser.add_argument(
        "--server-enabled",
        action=argparse.BooleanOptionalAction,
        default=values["server_enabled"],
        help="включить или отключить HTTP-сервер",
    )
    parser.add_argument(
        "--stream-enabled",
        action=argparse.BooleanOptionalAction,
        default=values["stream_enabled"],
        help="включить или отключить FFmpeg-поток",
    )
    parser.add_argument(
        "--splash-path",
        default=values["splash_path"],
        help="путь к изображению заставки при отключённом потоке",
    )
    parser.add_argument(
        "--ui-language",
        choices=UI_LANGUAGES,
        default=values["ui_language"],
        help="язык браузерного интерфейса: ru, uk, en или be",
    )
    parser.add_argument(
        "--audio-enabled",
        action=argparse.BooleanOptionalAction,
        default=values["audio_enabled"],
        help="передавать звук вместе с видео",
    )
    parser.add_argument(
        "--audio-source",
        default=values["audio_source"],
        help="источник PulseAudio для звука",
    )
    parser.add_argument("--audio-bitrate", type=int, default=values["audio_bitrate"], help="битрейт звука в кбит/с")
    parser.add_argument("--audio-sample-rate", type=int, default=values["audio_sample_rate"], help="частота дискретизации звука")
    parser.add_argument("--audio-channels", type=int, default=values["audio_channels"], help="число аудиоканалов")
    parser.add_argument("--ffmpeg", default=values["ffmpeg"], help="путь к исполняемому файлу FFmpeg")
    parser.add_argument("--token", default=values["token"], help="токен доступа HTTP и WebSocket")
    parser.add_argument(
        "--open-browser",
        action="store_true",
        dest="open_browser",
        default=values["open_browser"],
        help="открывать браузер автоматически",
    )
    parser.add_argument(
        "--no-browser",
        action="store_false",
        dest="open_browser",
        default=values["open_browser"],
        help="не открывать браузер автоматически",
    )
    args = parser.parse_args(arguments)

    if not isinstance(args.host, str) or not args.host.strip():
        raise ValueError("host должен быть непустой строкой")
    if not isinstance(args.port, int) or not 0 <= args.port <= 65535:
        raise ValueError("port должен быть в диапазоне 0..65535")
    if not isinstance(args.fps, int) or not 1 <= args.fps <= 120:
        raise ValueError("fps должен быть в диапазоне 1..120")
    if not isinstance(args.bitrate, int) or not 250 <= args.bitrate <= 100_000:
        raise ValueError("bitrate должен быть в диапазоне 250..100000")
    if args.encoder not in ENCODER_NAMES:
        raise ValueError("encoder должен быть auto, nvenc, qsv, amf, x264 или x265")
    for name, value in (("max-width", args.max_width), ("max-height", args.max_height)):
        if not isinstance(value, int) or (value != 0 and not 320 <= value <= 7680):
            raise ValueError(f"{name} должен быть 0 или в диапазоне 320..7680")
    if not isinstance(args.source, str) or not args.source.strip():
        raise ValueError("source должен быть непустой строкой")
    if args.capture_mode not in CAPTURE_MODES:
        raise ValueError("capture_mode должен быть screen, window, camera или voice")
    if args.video_codec not in VIDEO_CODEC_NAMES:
        raise ValueError("video_codec должен быть h264 или hevc")
    for name, value in (("window-id", args.window_id), ("camera-device", args.camera_device), ("audio-source", args.audio_source)):
        if not isinstance(value, str):
            raise ValueError(f"{name} должен быть строкой")
    if not 32 <= args.audio_bitrate <= 2000:
        raise ValueError("audio_bitrate должен быть в диапазоне 32..2000")
    if args.audio_sample_rate not in {44100, 48000, 96000}:
        raise ValueError("audio_sample_rate должен быть 44100, 48000 или 96000")
    if args.audio_channels not in {1, 2}:
        raise ValueError("audio_channels должен быть 1 или 2")
    if (
        not isinstance(args.audio_enabled, bool)
        or not isinstance(args.draw_mouse, bool)
        or not isinstance(args.server_enabled, bool)
        or not isinstance(args.stream_enabled, bool)
    ):
        raise ValueError("audio_enabled, draw_mouse, server_enabled и stream_enabled должны быть true или false")
    if not isinstance(args.splash_path, str):
        raise ValueError("splash_path должен быть строкой")
    splash_path = str(Path(args.splash_path).expanduser()) if args.splash_path.strip() else ""
    if splash_path and not Path(splash_path).is_absolute():
        raise ValueError("splash_path должен быть абсолютным путём")
    if not isinstance(args.ui_language, str) or args.ui_language not in UI_LANGUAGES:
        raise ValueError("ui_language должен быть одним из: ru, uk, en, be")
    if not isinstance(args.token, str) or (args.token and not TOKEN_PATTERN.fullmatch(args.token)):
        raise ValueError("token должен содержать 8–128 символов A–Z, a–z, 0–9, . _ ~ -")
    if not isinstance(args.open_browser, bool):
        raise ValueError("open_browser должен быть true или false")
    if args.ffmpeg is not None and not isinstance(args.ffmpeg, str):
        raise ValueError("ffmpeg должен быть строкой")

    return Settings(
        host=args.host.strip(),
        port=args.port,
        fps=args.fps,
        bitrate=args.bitrate,
        encoder=args.encoder,
        max_width=args.max_width,
        max_height=args.max_height,
        source=args.source.strip(),
        token=args.token,
        open_browser=args.open_browser,
        ffmpeg=args.ffmpeg.strip() if isinstance(args.ffmpeg, str) and args.ffmpeg.strip() else None,
        audio_enabled=args.audio_enabled,
        audio_source=args.audio_source.strip(),
        audio_bitrate=args.audio_bitrate,
        audio_sample_rate=args.audio_sample_rate,
        audio_channels=args.audio_channels,
        capture_mode=args.capture_mode,
        window_id=args.window_id.strip(),
        camera_device=args.camera_device.strip(),
        video_codec=args.video_codec,
        draw_mouse=args.draw_mouse,
        server_enabled=args.server_enabled,
        stream_enabled=args.stream_enabled,
        splash_path=splash_path,
        ui_language=args.ui_language,
    )
