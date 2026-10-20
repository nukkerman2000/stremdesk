from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import ctypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import SPLASH_SUFFIXES, VOICE_FALLBACK_SIZE, VOICE_FPS, Settings
from .hub import StreamHub


LOGGER = logging.getLogger(__name__)
ENCODER_ORDER = ("nvenc", "qsv", "amf", "x264", "x265")
WINDOWS_INPUT_ORDER = ("ddagrab", "gdigrab", "ddshow")
LINUX_INPUT_ORDER = ("x11grab",)
ENCODER_ACTUAL_NAMES = {
    ("h264", "nvenc"): "h264_nvenc",
    ("h264", "qsv"): "h264_qsv",
    ("h264", "amf"): "h264_amf",
    ("h264", "x264"): "libx264",
    ("hevc", "nvenc"): "hevc_nvenc",
    ("hevc", "qsv"): "hevc_qsv",
    ("hevc", "amf"): "hevc_amf",
    ("hevc", "x265"): "libx265",
}


def _input_order() -> tuple[str, ...]:
    return WINDOWS_INPUT_ORDER if os.name == "nt" else LINUX_INPUT_ORDER


def _default_input_name() -> str:
    return "gdigrab" if os.name == "nt" else "x11grab"


def _required_input_name(settings: Settings) -> str:
    if settings.capture_mode == "camera":
        return "dshow" if os.name == "nt" else "v4l2"
    if settings.capture_mode == "window" and os.name != "nt":
        return "x11grab"
    return _default_input_name()


def _software_encoder_name(video_codec: str) -> str:
    return "x265" if video_codec == "hevc" else "x264"


def _encoder_actual_name(encoder: str, video_codec: str) -> str:
    return ENCODER_ACTUAL_NAMES.get((video_codec, encoder), encoder)


def find_ffmpeg(settings: Settings) -> str:
    candidates: list[str] = []
    if settings.ffmpeg:
        candidates.append(settings.ffmpeg)
    root = Path(__file__).resolve().parents[1]
    if os.name == "nt":
        candidates.extend(
            [
                str(root / "runtime" / "ffmpeg" / "ffmpeg.exe"),
                str(root / "runtime" / "ffmpeg" / "bin" / "ffmpeg.exe"),
            ]
        )
    else:
        candidates.extend(
            [
                str(root / "runtime" / "ffmpeg" / "bin" / "ffmpeg"),
                str(root / "runtime" / "ffmpeg" / "ffmpeg"),
            ]
        )
    discovered = shutil.which("ffmpeg")
    if discovered:
        candidates.append(discovered)
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return str(path.resolve())
        resolved = shutil.which(candidate)
        if resolved:
            return str(Path(resolved).resolve())
    if os.name == "nt":
        message = "FFmpeg не найден. Укажите --ffmpeg или соберите portable-версию build-portable.ps1"
    else:
        message = "FFmpeg не найден. Установите ffmpeg в системе или укажите --ffmpeg"
    raise FileNotFoundError(message)


def _creation_flags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _die_with_parent() -> None:
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0)


def _child_options() -> dict:
    if os.name == "posix":
        return {"preexec_fn": _die_with_parent}
    return {}


def _run_probe(ffmpeg: str, *arguments: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, str(error)
    return result.returncode, f"{result.stdout}\n{result.stderr}"


def detect_encoders(ffmpeg: str, video_codec: str = "h264") -> list[str]:
    returncode, output = _run_probe(ffmpeg, "-encoders")
    if returncode != 0:
        return []
    found: list[str] = []
    candidates = ("nvenc", "qsv", "amf", _software_encoder_name(video_codec))
    for encoder in candidates:
        actual_name = _encoder_actual_name(encoder, video_codec)
        if re.search(rf"\b{re.escape(actual_name)}\b", output):
            found.append(encoder)
    return found


def detect_inputs(ffmpeg: str, settings: Settings | None = None) -> list[str]:
    input_names: tuple[str, ...]
    if settings is None:
        input_names = _input_order()
    else:
        input_names = (_required_input_name(settings),)
    found: list[str] = []
    for input_name in input_names:
        returncode, output = _run_probe(ffmpeg, "-h", f"demuxer={input_name}")
        if returncode == 0 and re.search(rf"Demuxer [^\n]*\b{re.escape(input_name)}\b", output):
            found.append(input_name)
    return found


def _scale_filter(settings: Settings, fps: int | None = None) -> str:
    parts = [f"fps={fps or settings.fps}"]
    if settings.max_width or settings.max_height:
        width = settings.max_width or 7680
        height = settings.max_height or 4320
        parts.append(
            f"scale=w='min({width},iw)':h='min({height},ih)':"
            "force_original_aspect_ratio=decrease"
        )
    parts.append("format=yuv420p")
    return ",".join(parts)


def _encoder_arguments(encoder: str, settings: Settings) -> list[str]:
    bitrate = f"{settings.bitrate}k"
    common = [
        "-b:v",
        bitrate,
        "-maxrate",
        bitrate,
        "-bufsize",
        f"{settings.bitrate * 2}k",
        "-g",
        str(max(1, settings.fps)),
        "-keyint_min",
        str(max(1, settings.fps)),
        "-bf",
        "0",
        "-sc_threshold",
        "0",
    ]
    actual_name = _encoder_actual_name(encoder, settings.video_codec)
    if encoder == "nvenc":
        return ["-c:v", actual_name, "-preset", "p1", "-tune", "ull", "-rc", "vbr", *common]
    if encoder == "qsv":
        return ["-c:v", actual_name, "-preset", "veryfast", "-low_delay", "1", *common]
    if encoder == "amf":
        return ["-c:v", actual_name, "-usage", "ultralowlatency", "-quality", "speed", *common]
    return ["-c:v", actual_name, "-preset", "ultrafast", "-tune", "zerolatency", *common]


def _capture_source(settings: Settings) -> str:
    source = settings.source.strip()
    if source.lower() in {"auto", "desktop", "screen"}:
        return os.environ.get("DISPLAY", ":0.0")
    return source


def _camera_source(settings: Settings) -> str:
    source = settings.camera_device.strip() or settings.source.strip()
    if not source or source.lower() in {"auto", "desktop", "screen"}:
        raise ValueError("Для захвата камеры укажите camera_device")
    return source


def voice_splash_image(settings: Settings) -> str:
    candidate = settings.splash_path.strip()
    if not candidate:
        return ""
    path = Path(candidate).expanduser()
    if path.suffix.lower() not in SPLASH_SUFFIXES or not path.is_file():
        return ""
    return str(path.resolve())


def voice_frame_rate(settings: Settings) -> int:
    return max(1, min(settings.fps, VOICE_FPS))


def voice_output_size(settings: Settings) -> tuple[int, int]:
    width = settings.max_width or VOICE_FALLBACK_SIZE[0]
    height = settings.max_height or VOICE_FALLBACK_SIZE[1]
    return width, height


def _voice_video_input_arguments(settings: Settings) -> list[str]:
    rate = str(voice_frame_rate(settings))
    image = voice_splash_image(settings)
    if image:
        return ["-loop", "1", "-framerate", rate, "-i", image]
    width, height = voice_output_size(settings)
    return ["-f", "lavfi", "-framerate", rate, "-i", f"color=c=black:s={width}x{height}:r={rate}"]


def _video_input_arguments(settings: Settings, input_name: str | None) -> list[str]:
    if settings.capture_mode == "voice":
        return _voice_video_input_arguments(settings)
    selected_input = input_name or _default_input_name()
    if settings.capture_mode == "camera":
        source = _camera_source(settings)
        if os.name == "nt":
            return ["-f", "dshow", "-i", f"video={source}"]
        return ["-f", "v4l2", "-framerate", str(settings.fps), "-i", source]
    if settings.capture_mode == "window" and os.name != "nt":
        if not settings.window_id.strip():
            raise ValueError("Для захвата окна укажите window_id")
        return [
            "-f",
            "x11grab",
            "-framerate",
            str(settings.fps),
            "-draw_mouse",
            "1" if settings.draw_mouse else "0",
            "-window_id",
            settings.window_id.strip(),
            "-i",
            _capture_source(settings),
        ]
    if selected_input == "x11grab":
        return [
            "-f",
            "x11grab",
            "-framerate",
            str(settings.fps),
            "-draw_mouse",
            "1" if settings.draw_mouse else "0",
            "-i",
            _capture_source(settings),
        ]
    if selected_input in WINDOWS_INPUT_ORDER:
        return ["-f", selected_input, "-framerate", str(settings.fps), "-i", settings.source]
    return ["-f", selected_input, "-i", settings.source]


def _audio_input_arguments(settings: Settings) -> list[str]:
    if not settings.audio_enabled:
        return []
    source = settings.audio_source.strip() or "default"
    if os.name == "nt":
        return ["-f", "dshow", "-i", f"audio={source}"]
    return ["-f", "pulse", "-i", source]


def build_ffmpeg_command(
    ffmpeg: str,
    settings: Settings,
    encoder: str,
    input_name: str | None = None,
) -> list[str]:
    command = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin"]
    voice_only = settings.capture_mode == "voice"
    command.extend(_video_input_arguments(settings, input_name))
    audio_input = _audio_input_arguments(settings)
    command.extend(audio_input)
    command.extend(["-map", "0:v:0"])
    if audio_input:
        command.extend(
            [
                "-map",
                "1:a:0",
                "-c:a",
                "aac",
                "-b:a",
                f"{settings.audio_bitrate}k",
                "-ar",
                str(settings.audio_sample_rate),
                "-ac",
                str(settings.audio_channels),
            ]
        )
    else:
        command.append("-an")
    command.extend(
        [
            "-sn",
            "-dn",
            "-vf",
            _scale_filter(settings, voice_frame_rate(settings) if voice_only else None),
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "0",
            *_encoder_arguments(encoder, settings),
            "-movflags",
            "+frag_keyframe+empty_moov+default_base_moof",
            "-fflags",
            "+nobuffer",
            "-flags",
            "+low_delay",
            "-flush_packets",
            "1",
        ]
    )
    if audio_input:
        command.append("-shortest")
    command.extend(["-f", "mp4", "pipe:1"])
    return command


def build_hls_command(ffmpeg: str, hls_dir: str) -> list[str]:
    """Ремукс уже закодированного fMP4 в HLS-сегменты.

    Камера держит одного клиента: если HLS-кодировщик открывает /dev/video0
    вторым, основной поток падает с "Device or resource busy". Поэтому HLS
    получает готовый поток на stdin и только переупаковывает его без
    перекодирования — устройство захвата открывается ровно один раз.
    """
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-fflags",
        "+nobuffer",
        "-i",
        "pipe:0",
        "-c",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        "1",
        "-hls_list_size",
        "6",
        "-hls_delete_threshold",
        "12",
        "-hls_flags",
        "delete_segments+append_list+omit_endlist+independent_segments+temp_file",
        "-hls_segment_type",
        "mpegts",
        "-hls_segment_filename",
        str(Path(hls_dir) / "seg_%06d.ts"),
        str(Path(hls_dir) / "index.m3u8"),
    ]


class FFmpegStreamer:
    def __init__(
        self,
        settings: Settings,
        hub: StreamHub,
        status_callback: Callable[[dict], None] | None = None,
    ) -> None:
        self.settings = settings
        self.hub = hub
        self._status_callback = status_callback
        self._lock = threading.RLock()
        self._reload_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._ffmpeg_path = ""
        self._hls_dir: Path | None = None
        self._hls_process: subprocess.Popen[bytes] | None = None
        self._available_encoders: list[str] = []
        self._available_inputs: list[str] = []
        self._failed_encoders: set[str] = set()
        self._status: dict = {
            "state": "idle",
            "active_encoder": None,
            "available_encoders": [],
            "available_inputs": [],
            "ffmpeg": None,
            "pid": None,
            "hls_pid": None,
            "bytes": 0,
            "last_error": None,
            "started_at": None,
            "capture_mode": settings.capture_mode,
            "voice_only": settings.capture_mode == "voice",
            "video_codec": settings.video_codec,
            "audio_enabled": settings.audio_enabled,
            "audio_source": settings.audio_source,
            "stream_enabled": settings.stream_enabled,
            "splash_path": settings.splash_path,
        }

    @property
    def ffmpeg_path(self) -> str:
        return self._ffmpeg_path

    @property
    def hls_dir(self) -> Path | None:
        return self._hls_dir

    def _prepare_hls_dir(self) -> None:
        if self._hls_dir is None:
            self._hls_dir = Path(tempfile.mkdtemp(prefix="stremdesk-hls-"))
        shutil.rmtree(self._hls_dir, ignore_errors=True)
        self._hls_dir.mkdir(parents=True, exist_ok=True)

    def _cleanup_hls_dir(self) -> None:
        if self._hls_dir is None:
            return
        shutil.rmtree(self._hls_dir, ignore_errors=True)
        self._hls_dir = None

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            if not self.settings.stream_enabled:
                self._update(
                    state="disabled",
                    active_encoder=None,
                    pid=None,
                    ffmpeg=None,
                    last_error=None,
                    stream_enabled=False,
                    splash_path=self.settings.splash_path,
                )
                return
            self._stop_event.clear()
            self._failed_encoders.clear()
            self._thread = threading.Thread(
                target=self._bootstrap,
                name="stremdesk-ffmpeg",
                daemon=True,
            )
            self._thread.start()

    def update_settings(self, settings: Settings) -> None:
        with self._reload_lock:
            with self._lock:
                if settings == self.settings:
                    return
                self.settings = settings
            self.stop()
            self.start()

    def _bootstrap(self) -> None:
        try:
            ffmpeg_path = find_ffmpeg(self.settings)
            with self._lock:
                self._ffmpeg_path = ffmpeg_path
            available_encoders = detect_encoders(ffmpeg_path, self.settings.video_codec)
            available_inputs = detect_inputs(ffmpeg_path, self.settings)
            if not available_encoders:
                raise RuntimeError(
                    f"FFmpeg не содержит поддерживаемого {self.settings.video_codec.upper()}-энкодера"
                )
            if not available_inputs:
                raise RuntimeError(
                    f"FFmpeg не содержит входа {_required_input_name(self.settings)} для захвата"
                )
            with self._lock:
                self._available_encoders = available_encoders
                self._available_inputs = available_inputs
            self._update(
                ffmpeg=ffmpeg_path,
                available_encoders=list(available_encoders),
                available_inputs=list(available_inputs),
                last_error=None,
                capture_mode=self.settings.capture_mode,
                voice_only=self.settings.capture_mode == "voice",
                video_codec=self.settings.video_codec,
                audio_enabled=self.settings.audio_enabled,
                audio_source=self.settings.audio_source,
                stream_enabled=self.settings.stream_enabled,
                splash_path=self.settings.splash_path,
            )
            if self._stop_event.is_set():
                return
            self._run()
        except Exception as error:
            with self._lock:
                self._process = None
            self._update(
                state="error",
                active_encoder=None,
                pid=None,
                last_error=str(error),
            )

    def set_error(self, message: str) -> None:
        self._update(state="error", active_encoder=None, pid=None, last_error=message)

    def stop(self) -> None:
        self._stop_event.set()
        self._stop_hls()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        with self._lock:
            self._process = None
            self._thread = None
            self._update(state="stopped", active_encoder=None, pid=None, hls_pid=None)
        self._cleanup_hls_dir()

    def status(self) -> dict:
        with self._lock:
            result = dict(self._status)
            result["clients"] = self.hub.client_count
            result["ready"] = self.hub.ready
            return result

    def _run(self) -> None:
        input_index = 0
        while not self._stop_event.is_set():
            if not self.settings.stream_enabled:
                self._update(
                    state="disabled",
                    active_encoder=None,
                    pid=None,
                    ffmpeg=None,
                    last_error=None,
                    stream_enabled=False,
                    splash_path=self.settings.splash_path,
                )
                return
            encoder = self._choose_encoder()
            if encoder is None:
                self._stop_hls()
                self._update(state="error", last_error=f"нет доступного {self.settings.video_codec.upper()}-энкодера")
                return
            input_name = self._available_inputs[min(input_index, len(self._available_inputs) - 1)]
            self._prepare_hls_dir()
            self._stop_hls()
            self._start_hls()
            command = build_ffmpeg_command(self._ffmpeg_path, self.settings, encoder, input_name)
            self._update(
                state="starting",
                active_encoder=encoder,
                last_error=None,
            )
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    creationflags=_creation_flags(),
                    **_child_options(),
                )
            except OSError as error:
                self._failed_encoders.add(encoder)
                self._update(state="reconnecting", last_error=str(error))
                self._stop_event.wait(1)
                continue

            with self._lock:
                self._process = process
            self.hub.reset()
            self._update(
                state="streaming",
                pid=process.pid,
                started_at=datetime.now(timezone.utc).isoformat(),
                bytes=0,
            )
            stderr_tail: list[str] = []
            stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(process, stderr_tail),
                name="stremdesk-ffmpeg-stderr",
                daemon=True,
            )
            stderr_thread.start()
            received = False
            byte_count = 0
            while not self._stop_event.is_set():
                assert process.stdout is not None
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                received = True
                byte_count += len(chunk)
                self.hub.feed(chunk)
                self._feed_hls(chunk)
                self._update(bytes=byte_count)
            if self._stop_event.is_set():
                break
            return_code = process.wait()
            stderr_thread.join(timeout=1)
            error_text = " ".join(line.strip() for line in stderr_tail if line.strip())[-800:]
            with self._lock:
                self._process = None
            if not received or return_code != 0:
                self._failed_encoders.add(encoder)
            self._update(
                state="reconnecting",
                active_encoder=None,
                pid=None,
                last_error=error_text or f"FFmpeg завершился с кодом {return_code}",
            )
            self._stop_event.wait(1)
            self._stop_hls()
            input_index = 0


    def _start_hls(self) -> None:
        if self._hls_dir is None or self._stop_event.is_set():
            return
        command = build_hls_command(self._ffmpeg_path, str(self._hls_dir))
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                creationflags=_creation_flags(),
                **_child_options(),
            )
        except OSError:
            # HLS — дополнение; основной поток продолжает работать без него
            return
        with self._lock:
            self._hls_process = process
        self._update(hls_pid=process.pid)

    def _stop_hls(self) -> None:
        with self._lock:
            process = self._hls_process
            self._hls_process = None
        if process is None or process.poll() is not None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()

    def _feed_hls(self, chunk: bytes) -> None:
        """Скармливает кусок закодированного потока в HLS-ремуксер.

        Ошибка здесь не должна влиять на основной поток: если ремуксер упал,
        HLS просто отключается, а видео продолжает идти подписчикам как есть.
        """
        with self._lock:
            process = self._hls_process
        if process is None or process.stdin is None:
            return
        if process.poll() is not None:
            self._stop_hls()
            return
        try:
            process.stdin.write(chunk)
        except (BrokenPipeError, ValueError, OSError):
            self._stop_hls()

    def _choose_encoder(self) -> str | None:
        with self._lock:
            available = set(self._available_encoders)
            failed = set(self._failed_encoders)
            video_codec = self.settings.video_codec
            configured_encoder = self.settings.encoder
        if configured_encoder != "auto":
            candidates = [configured_encoder]
        else:
            candidates = ["nvenc", "qsv", "amf", _software_encoder_name(video_codec)]
        for candidate in candidates:
            if candidate in available and candidate not in failed:
                return candidate
        return None

    @staticmethod
    def _drain_stderr(process: subprocess.Popen[bytes], tail: list[str]) -> None:
        if process.stderr is None:
            return
        for line in iter(process.stderr.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                tail.append(text)
                del tail[:-8]

    def _update(self, **values: object) -> None:
        with self._lock:
            self._status.update(values)
            result = dict(self._status)
        if self._status_callback is not None:
            self._status_callback(result)
