from __future__ import annotations

import logging
import signal
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from .config import Settings, config_path_from_args, parse_settings
from .http_server import DesktopHTTPServer, ServerContext
from .hub import StreamHub
from .stream import FFmpegStreamer


LOGGER = logging.getLogger("stremdesk")


def _browser_host(settings: Settings) -> str:
    if settings.host == "0.0.0.0":
        return "127.0.0.1"
    if settings.host == "::":
        return "::1"
    return settings.host


def _format_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _with_query(url: str, token: str) -> str:
    return f"{url}?token={quote(token, safe='')}" if token else url


class ServerManager:
    def __init__(self, context: ServerContext) -> None:
        self.context = context
        self._lock = threading.RLock()
        self._server: DesktopHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._address: tuple[str, int] | None = None
        self._settings: Settings | None = None

    def start(self, settings: Settings) -> None:
        self.update(settings)

    def update(self, settings: Settings) -> None:
        with self._lock:
            if not settings.server_enabled:
                self._stop_locked()
                self.context.settings = settings
                self._settings = settings
                return
            address = (settings.host, settings.port)
            if self._server is not None and self._address == address:
                self.context.settings = settings
                self._settings = settings
                return
            replacement = DesktopHTTPServer(address, self.context)
            actual_port = int(replacement.server_address[1])
            self._stop_locked()
            self.context.settings = settings
            self.context.port = actual_port
            self._settings = settings
            self._address = address
            self._server = replacement
            self._thread = threading.Thread(
                target=replacement.serve_forever,
                kwargs={"poll_interval": 0.5},
                name="stremdesk-http",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        self._address = None
        if server is None:
            return
        if thread is not None and thread.is_alive():
            server.shutdown()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        server.server_close()


class ConfigWatcher:
    def __init__(
        self,
        path: Path,
        arguments: list[str],
        callback: Callable[[Settings], None],
        interval: float = 0.5,
    ) -> None:
        self.path = path
        self.arguments = arguments
        self.callback = callback
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._signature = self._read_signature()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="stremdesk-config-watcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None

    def _read_signature(self) -> tuple[int, int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size, stat.st_ino

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval):
            signature = self._read_signature()
            if signature == self._signature:
                continue
            self._signature = signature
            try:
                settings = parse_settings(self.arguments)
            except ValueError as error:
                LOGGER.error("Не удалось применить конфигурацию: %s", error)
                continue
            self.callback(settings)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        settings = parse_settings(arguments)
    except ValueError as error:
        print(f"Ошибка конфигурации: {error}", file=sys.stderr)
        return 2
    config_path = config_path_from_args(arguments)
    token = settings.token

    hub = StreamHub(history_size=2)
    reported_status: tuple[object, ...] | None = None

    def report_status(status: dict) -> None:
        nonlocal reported_status
        state = status.get("state")
        if state not in {"streaming", "error", "reconnecting"}:
            return
        status_key = (
            state,
            status.get("active_encoder"),
            status.get("last_error"),
            status.get("clients", 0),
        )
        if status_key == reported_status:
            return
        reported_status = status_key
        detail = status.get("last_error") or ""
        LOGGER.info(
            "stream=%s encoder=%s clients=%s%s",
            state,
            status.get("active_encoder") or "-",
            status.get("clients", 0),
            f" {detail}" if detail else "",
        )

    streamer = FFmpegStreamer(settings, hub, report_status)
    context = ServerContext(settings=settings, hub=hub, streamer=streamer, port=settings.port)
    server_manager = ServerManager(context)
    try:
        server_manager.start(settings)
    except OSError as error:
        print(f"Не удалось запустить HTTP-сервер: {error}", file=sys.stderr)
        return 1
    try:
        streamer.start()
    except Exception as error:
        streamer.set_error(str(error))
        LOGGER.error("FFmpeg не запущен: %s", error)

    host = _format_host(_browser_host(settings))
    base_url = f"http://{host}:{context.port}"
    browser_url = _with_query(f"{base_url}/", token)
    vlc_url = _with_query(f"{base_url}/vlc.mp4", token)
    print("StremDesk запущен")
    if settings.server_enabled:
        print(f"Браузер: {browser_url}")
        print(f"VLC:     {vlc_url}")
    else:
        print("HTTP-сервер выключен")
    if settings.host in {"0.0.0.0", "::"}:
        print("Для другого устройства замените 127.0.0.1 на IP этого компьютера.")
    print(f"FFmpeg:  {streamer.ffmpeg_path or 'определяется в фоне'}")
    print("Остановка: Ctrl+C")

    if settings.open_browser and settings.server_enabled:
        try:
            webbrowser.open(browser_url, new=2)
        except Exception as error:
            LOGGER.warning("Не удалось открыть браузер: %s", error)

    reload_lock = threading.RLock()

    def apply_settings(new_settings: Settings) -> None:
        with reload_lock:
            old_settings = context.settings
            if old_settings == new_settings:
                return
            try:
                server_manager.update(new_settings)
            except OSError as error:
                LOGGER.error("Настройки сервера не применены: %s", error)
                return
            stream_fields = {
                "host",
                "port",
                "token",
                "open_browser",
                "server_enabled",
                "ui_language",
            }
            if any(
                getattr(old_settings, field) != getattr(new_settings, field)
                for field in Settings.__dataclass_fields__
                if field not in stream_fields
            ):
                streamer.update_settings(new_settings)
            LOGGER.info(
                "Настройки применены: capture=%s codec=%s audio=%s stream=%s server=%s",
                new_settings.capture_mode,
                new_settings.video_codec,
                "on" if new_settings.audio_enabled else "off",
                "on" if new_settings.stream_enabled else "off",
                "on" if new_settings.server_enabled else "off",
            )

    watcher = ConfigWatcher(config_path, arguments, apply_settings)
    watcher.start()
    stop_event = threading.Event()
    for signal_name in ("SIGTERM", "SIGINT", "SIGHUP"):
        handler_signal = getattr(signal, signal_name, None)
        if handler_signal is None:
            continue
        try:
            signal.signal(handler_signal, lambda *_: stop_event.set())
        except (OSError, ValueError):
            continue
    try:
        while not stop_event.wait(0.5):
            pass
        print("\nОстановка...")
    except KeyboardInterrupt:
        print("\nОстановка...")
    finally:
        watcher.stop()
        streamer.stop()
        server_manager.stop()
    return 0
