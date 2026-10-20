from __future__ import annotations

import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlencode

from PyQt6.QtCore import QProcess, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop_streamer.config import Settings, config_path_from_args, parse_settings


def _output_lines(command: list[str]) -> list[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=4, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _server_is_running(settings: Settings) -> bool:
    if not settings.server_enabled or settings.port == 0:
        return False
    host = settings.host
    if host == "0.0.0.0":
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        with urllib.request.urlopen(f"http://{host}:{settings.port}/healthz", timeout=0.4) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("ok") is True


def _server_arguments(arguments: list[str], config_path: Path) -> list[str]:
    result = [str(Path(__file__).with_name("stremdesk.py")), "--config", str(config_path.expanduser().resolve())]
    skip_next = False
    for argument in arguments:
        if skip_next:
            skip_next = False
            continue
        if argument == "--config":
            skip_next = True
            continue
        if argument.startswith("--config="):
            continue
        result.append(argument)
    return result


def discover_windows() -> list[tuple[str, str]]:
    windows: list[tuple[str, str]] = []
    for window_id in _output_lines(["xdotool", "search", "--onlyvisible", "--name", ""]):
        if not re.fullmatch(r"\d+", window_id):
            continue
        title = " ".join(_output_lines(["xdotool", "getwindowname", window_id]))
        windows.append((window_id, title or f"Окно {window_id}"))
    return windows


def discover_cameras() -> list[str]:
    def sort_key(value: str) -> tuple[int, str]:
        match = re.search(r"(\d+)$", value)
        return int(match.group(1)) if match else 0, value

    return sorted(glob.glob("/dev/video*"), key=sort_key)


def discover_audio_sources() -> list[str]:
    sources = ["default"]
    commands = (
        (["pactl", "list", "short", "sources"], ""),
        (["pactl", "list", "short", "sinks"], ".monitor"),
    )
    for command, suffix in commands:
        for line in _output_lines(command):
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[1].strip() + suffix
            if name and name not in sources:
                sources.append(name)
    return sources


def audio_source_label(source: str) -> str:
    if source == "default":
        return "По умолчанию PulseAudio (обычно микрофон)"
    if source.endswith(".monitor"):
        return f"Системный вывод: {source}"
    return f"Микрофон/вход: {source}"


class SettingsWindow(QMainWindow):
    def __init__(self, arguments: list[str]) -> None:
        super().__init__()
        self.arguments = arguments
        self.config_path = config_path_from_args(arguments)
        self.server_process: QProcess | None = None
        self._loading = False
        self._shutting_down = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_connections)
        self._last_status_bytes = 0
        self._last_status_time: float | None = None
        self._bitrate_kbps = 0.0
        self.setWindowTitle("StremDesk — настройки")
        self.setMinimumSize(720, 620)
        self._build_ui()
        self._populate_devices()
        self._load()
        self._connect_signals()
        QTimer.singleShot(0, self._ensure_server_process)
        QTimer.singleShot(0, self._refresh_connections)
        self._status_timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)

        server_group = QGroupBox("Сервер и подключение")
        server_form = QFormLayout(server_group)
        self.server_enabled = QCheckBox("Выключить сервер")
        self.host = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setSpecialValueText("0 (выбрать свободный)")
        self.token = QLineEdit()
        self.token.setPlaceholderText("необязательно")
        self.open_browser = QCheckBox("Открывать браузер при запуске")
        server_hint = QLabel("Галочка выключает только HTTP-сервер; снимите её, чтобы сервер поднялся.")
        server_hint.setWordWrap(True)
        server_form.addRow(self.server_enabled)
        server_form.addRow("Адрес", self.host)
        server_form.addRow("Порт", self.port)
        server_form.addRow("Токен", self.token)
        server_form.addRow(self.open_browser)
        server_form.addRow(server_hint)
        root_layout.addWidget(server_group)

        tabs = QTabWidget()
        self.tabs = tabs
        root_layout.addWidget(tabs, 1)

        connections_tab = QWidget()
        connections_layout = QVBoxLayout(connections_tab)
        state_group = QGroupBox("Состояние в реальном времени")
        state_form = QFormLayout(state_group)
        self.conn_stream_state = QLabel("Ожидание данных…")
        self.conn_summary = QLabel("—")
        self.conn_stream_rate = QLabel("—")
        self.conn_encoder = QLabel("—")
        self.conn_last_error = QLabel("—")
        self.conn_last_error.setWordWrap(True)
        state_form.addRow("Поток", self.conn_stream_state)
        state_form.addRow("Клиенты", self.conn_summary)
        state_form.addRow("Битрейт", self.conn_stream_rate)
        state_form.addRow("Кодирование", self.conn_encoder)
        state_form.addRow("Ошибка", self.conn_last_error)
        connections_layout.addWidget(state_group)

        self.connections_table = QTableWidget(0, 8)
        self.connections_table.setHorizontalHeaderLabels(
            ["#", "IP-адрес", "Порт", "Транспорт", "User-Agent", "Подключено", "Без данных", "Отдано"]
        )
        self.connections_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.connections_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.connections_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.connections_table.verticalHeader().setVisible(False)
        self.connections_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.connections_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        connections_layout.addWidget(self.connections_table, 1)

        connections_hint = QLabel(
            "Данные обновляются раз в секунду из /api/status. Настройки отсюда не меняются: "
            "они хранятся в config.json и применяются при запуске без графического интерфейса."
        )
        connections_hint.setWordWrap(True)
        connections_buttons = QHBoxLayout()
        refresh_status_button = QPushButton("Обновить сейчас")
        refresh_status_button.clicked.connect(self._refresh_connections)
        connections_buttons.addWidget(refresh_status_button)
        connections_buttons.addWidget(connections_hint, 1)
        connections_layout.addLayout(connections_buttons)
        tabs.addTab(connections_tab, "Соединения")

        capture_tab = QWidget()
        capture_form = QFormLayout(capture_tab)
        self.capture_mode = QComboBox()
        for value, title in (("screen", "Весь экран"), ("window", "Окно"), ("camera", "Камера"), ("voice", "Только звук (только голос)")):
            self.capture_mode.addItem(title, value)
        self.source = QLineEdit()
        self.source.setPlaceholderText("auto или :0.0")
        self.camera = QComboBox()
        self.camera.setEditable(True)
        self.camera.setPlaceholderText("/dev/video0")
        refresh_devices = QPushButton("Обновить источники")
        refresh_devices.clicked.connect(self._populate_devices)
        self.splash_path = QLineEdit()
        self.splash_path.setPlaceholderText("изображение, которое будет в видеотреке")
        splash_browse = QPushButton("Выбрать изображение")
        splash_browse.clicked.connect(self._browse_splash)
        splash_row_widget = QWidget()
        splash_row_layout = QHBoxLayout(splash_row_widget)
        splash_row_layout.setContentsMargins(0, 0, 0, 0)
        splash_row_layout.addWidget(self.splash_path)
        splash_row_layout.addWidget(splash_browse)
        voice_hint = QLabel(
            "В режиме «Только звук» кадры не захватываются: в поток идёт только звук, "
            "а видеотреком передаётся выбранная картинка. Если картинка не выбрана — чёрный экран."
        )
        voice_hint.setWordWrap(True)
        capture_form.addRow("Режим", self.capture_mode)
        capture_form.addRow("Дисплей / источник", self.source)
        capture_form.addRow("Камера", self.camera)
        capture_form.addRow(refresh_devices)
        capture_form.addRow("Картинка заставки", splash_row_widget)
        capture_form.addRow(voice_hint)
        self._capture_form = capture_form
        self._splash_row = capture_form.rowCount() - 2
        self._voice_hint_row = capture_form.rowCount() - 1
        capture_form.setRowVisible(self._splash_row, False)
        capture_form.setRowVisible(self._voice_hint_row, False)
        tabs.addTab(capture_tab, "Захват")

        window_tab = QWidget()
        window_form = QFormLayout(window_tab)
        self.window_combo = QComboBox()
        self.window_combo.addItem("Окно не выбрано", "")
        self.window_combo.currentIndexChanged.connect(self._window_selected)
        self.window_id = QLineEdit()
        self.window_id.setPlaceholderText("X11 window ID")
        self.pick_window = QPushButton("Выбрать окно мышью")
        self.pick_window.clicked.connect(self._pick_window_with_mouse)
        self.draw_mouse = QCheckBox("Показывать курсор мыши")
        window_form.addRow("Окно", self.window_combo)
        window_form.addRow("ID окна", self.window_id)
        window_form.addRow(self.pick_window)
        window_form.addRow(self.draw_mouse)
        tabs.addTab(window_tab, "Окно")

        audio_tab = QWidget()
        audio_form = QFormLayout(audio_tab)
        self.audio_enabled = QCheckBox("Передавать звук")
        self.audio_source = QComboBox()
        self.audio_source.setEditable(True)
        self.audio_source.setPlaceholderText("default или PulseAudio source")
        audio_hint = QLabel("Микрофон выбирается из списка «Микрофон/вход», системный звук — из списка «Системный вывод» (источник .monitor).")
        audio_hint.setWordWrap(True)
        refresh_audio = QPushButton("Обновить аудиоисточники")
        refresh_audio.clicked.connect(self._refresh_audio)
        self.audio_bitrate = QSpinBox()
        self.audio_bitrate.setRange(32, 2000)
        self.audio_bitrate.setSuffix(" кбит/с")
        self.audio_sample_rate = QComboBox()
        for value in (44100, 48000, 96000):
            self.audio_sample_rate.addItem(f"{value} Hz", value)
        self.audio_channels = QComboBox()
        self.audio_channels.addItem("Моно", 1)
        self.audio_channels.addItem("Стерео", 2)
        audio_form.addRow(self.audio_enabled)
        audio_form.addRow("Источник", self.audio_source)
        audio_form.addRow(audio_hint)
        audio_form.addRow(refresh_audio)
        audio_form.addRow("Битрейт", self.audio_bitrate)
        audio_form.addRow("Частота", self.audio_sample_rate)
        audio_form.addRow("Каналы", self.audio_channels)
        tabs.addTab(audio_tab, "Звук")

        video_tab = QWidget()
        video_form = QFormLayout(video_tab)
        self.ffmpeg = QLineEdit()
        self.ffmpeg.setPlaceholderText("оставить пустым для системного FFmpeg")
        self.video_codec = QComboBox()
        self.video_codec.addItem("H.264 / AVC", "h264")
        self.video_codec.addItem("H.265 / HEVC", "hevc")
        self.encoder = QComboBox()
        for value, title in (
            ("auto", "Авто"),
            ("nvenc", "NVIDIA NVENC"),
            ("qsv", "Intel QSV"),
            ("amf", "AMD AMF"),
            ("x264", "x264 программно"),
            ("x265", "x265 программно"),
        ):
            self.encoder.addItem(title, value)
        self.fps = QSpinBox()
        self.fps.setRange(1, 120)
        self.bitrate = QSpinBox()
        self.bitrate.setRange(250, 100000)
        self.bitrate.setSuffix(" кбит/с")
        self.max_width = QSpinBox()
        self.max_width.setRange(0, 7680)
        self.max_width.setSpecialValueText("0 (без ограничения)")
        self.max_height = QSpinBox()
        self.max_height.setRange(0, 7680)
        self.max_height.setSpecialValueText("0 (без ограничения)")
        video_form.addRow("FFmpeg", self.ffmpeg)
        video_form.addRow("Видеокодек", self.video_codec)
        video_form.addRow("Энкодер", self.encoder)
        video_form.addRow("FPS", self.fps)
        video_form.addRow("Видеобитрейт", self.bitrate)
        video_form.addRow("Макс. ширина", self.max_width)
        video_form.addRow("Макс. высота", self.max_height)
        tabs.addTab(video_tab, "Кодирование")

        stream_tab = QWidget()
        stream_form = QFormLayout(stream_tab)
        self.stream_enabled = QCheckBox("Выключить стрим")
        stream_hint = QLabel(
            "Снимите галочку, чтобы запустить FFmpeg; при выключенном стриме браузер показывает заставку. "
            "Картинка заставки выбирается на вкладке «Захват»."
        )
        stream_hint.setWordWrap(True)
        stream_form.addRow(self.stream_enabled)
        stream_form.addRow(stream_hint)
        tabs.addTab(stream_tab, "Трансляция")

        interface_tab = QWidget()
        interface_form = QFormLayout(interface_tab)
        self.ui_language = QComboBox()
        for value, title in (("ru", "Русский"), ("uk", "Українська"), ("en", "English"), ("be", "Беларуская")):
            self.ui_language.addItem(title, value)
        language_hint = QLabel("Язык применяется к браузерной странице просмотра.")
        language_hint.setWordWrap(True)
        interface_form.addRow("Язык браузера", self.ui_language)
        interface_form.addRow(language_hint)
        tabs.addTab(interface_tab, "Интерфейс")


        controls = [
            self.server_enabled,
            self.host,
            self.port,
            self.token,
            self.open_browser,
            self.ffmpeg,
            self.capture_mode,
            self.source,
            self.window_combo,
            self.window_id,
            self.camera,
            self.draw_mouse,
            self.audio_enabled,
            self.audio_source,
            self.audio_bitrate,
            self.audio_sample_rate,
            self.audio_channels,
            self.video_codec,
            self.encoder,
            self.fps,
            self.bitrate,
            self.max_width,
            self.max_height,
            self.stream_enabled,
            self.splash_path,
            self.ui_language,
        ]
        self._controls = controls
        self.auto_save = QCheckBox("Сохранять и применять автоматически")
        self.auto_save.setChecked(True)
        self._controls.append(self.auto_save)
        apply_button = QPushButton("Применить и сохранить")
        apply_button.clicked.connect(self._save)
        reload_button = QPushButton("Перечитать")
        reload_button.clicked.connect(self._load)
        buttons = QHBoxLayout()
        buttons.addWidget(QLabel("Изменения применяются к запущенному StremDesk автоматически"))
        buttons.addStretch(1)
        buttons.addWidget(reload_button)
        buttons.addWidget(apply_button)
        root_layout.addLayout(buttons)
        self.stop_on_close = QCheckBox("Останавливать StremDesk при закрытии окна")
        self.stop_on_close.setChecked(True)
        self.stop_on_close.setToolTip("Гасит сервер и стрим, даже если он был запущен не из этого окна")
        root_layout.addWidget(self.stop_on_close)
        self._update_capture_controls()

    def _connect_signals(self) -> None:
        for control in self._controls:
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._schedule_save)
                if control.isEditable():
                    control.currentTextChanged.connect(self._schedule_save)
            elif isinstance(control, QCheckBox):
                control.toggled.connect(self._schedule_save)
            else:
                control.textChanged.connect(self._schedule_save) if isinstance(control, QLineEdit) else control.valueChanged.connect(self._schedule_save)
        self.capture_mode.currentIndexChanged.connect(self._update_capture_controls)

    def _status_url(self) -> str:
        host = self.host.text().strip() or "127.0.0.1"
        if host == "0.0.0.0":
            host = "127.0.0.1"
        elif host == "::":
            host = "::1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        url = f"http://{host}:{self.port.value()}/api/status"
        token = self.token.text().strip()
        if token:
            url = f"{url}?{urlencode({'token': token})}"
        return url

    @staticmethod
    def _format_bytes(value: object) -> str:
        try:
            size = max(0.0, float(value))
        except (TypeError, ValueError):
            return "—"
        for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
            if size < 1024 or unit == "ТБ":
                return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
            size /= 1024
        return "—"

    @staticmethod
    def _format_duration(value: object) -> str:
        try:
            seconds = max(0, int(float(value)))
        except (TypeError, ValueError):
            return "—"
        if seconds < 60:
            return f"{seconds} с"
        minutes, rest = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes} мин {rest} с"
        hours, minutes = divmod(minutes, 60)
        return f"{hours} ч {minutes} мин"

    @staticmethod
    def _transport_label(value: object) -> str:
        transport = str(value or "")
        if transport == "websocket":
            return "WebSocket (MSE)"
        if transport == "vlc":
            return "VLC / MP4"
        return transport or "—"

    @staticmethod
    def _plural(value: int, one: str, few: str, many: str) -> str:
        if value % 10 == 1 and value % 100 != 11:
            return one
        if 2 <= value % 10 <= 4 and not 12 <= value % 100 <= 14:
            return few
        return many

    def _update_stream_rate(self, total_bytes: int) -> None:
        now = time.monotonic()
        if self._last_status_time is None:
            self._bitrate_kbps = 0.0
        elif total_bytes >= self._last_status_bytes:
            elapsed = now - self._last_status_time
            if elapsed > 0:
                self._bitrate_kbps = max(
                    0.0,
                    (total_bytes - self._last_status_bytes) * 8 / elapsed / 1000,
                )
        else:
            self._bitrate_kbps = 0.0
        self._last_status_bytes = total_bytes
        self._last_status_time = now

    def _refresh_connections(self) -> None:
        if self.server_enabled.isChecked() or self.port.value() == 0:
            self.conn_stream_state.setText("сервер выключен")
            self.conn_summary.setText("—")
            self.conn_stream_rate.setText("—")
            self.conn_encoder.setText("—")
            self.conn_last_error.setText("—")
            self.connections_table.setRowCount(0)
            self._last_status_bytes = 0
            self._last_status_time = None
            return
        try:
            with urllib.request.urlopen(self._status_url(), timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as error:
            self.conn_stream_state.setText("сервер недоступен")
            self.conn_summary.setText("—")
            self.conn_stream_rate.setText("—")
            self.conn_last_error.setText(str(error) or "нет ответа от /api/status")
            return
        if not isinstance(payload, dict):
            self.conn_stream_state.setText("некорректный ответ сервера")
            return
        connections = payload.get("connections")
        if not isinstance(connections, list):
            connections = []
        try:
            total_bytes = int(payload.get("bytes") or 0)
        except (TypeError, ValueError):
            total_bytes = 0
        self._update_stream_rate(total_bytes)
        ready_text = "готов" if payload.get("ready") else "не готов"
        audio_text = "со звуком" if payload.get("audio_enabled") else "без звука"
        self.conn_stream_state.setText(
            f"{payload.get('state', '—')} · {ready_text} · {audio_text}"
        )
        connections_count = int(payload.get("connection_count", len(connections)) or 0)
        clients_count = int(payload.get("clients", 0) or 0)
        self.conn_summary.setText(
            f"{connections_count} {self._plural(connections_count, 'подключение', 'подключения', 'подключений')}"
            f" · {clients_count} {self._plural(clients_count, 'уникальный IP', 'уникальных IP', 'уникальных IP')}"
        )
        self.conn_stream_rate.setText(f"{self._bitrate_kbps:.0f} кбит/с · всего {self._format_bytes(total_bytes)}")
        self.conn_encoder.setText(
            f"{payload.get('video_codec', '—')} / {payload.get('active_encoder') or payload.get('encoder') or '—'}"
        )
        last_error = payload.get("last_error")
        self.conn_last_error.setText(str(last_error) if last_error else "нет")
        self._fill_connections_table(connections)

    def _fill_connections_table(self, connections: list) -> None:
        rows = [item for item in connections if isinstance(item, dict)]
        rows.sort(key=lambda item: (str(item.get("host", "")), int(item.get("port") or 0)))
        self.connections_table.setUpdatesEnabled(False)
        try:
            self.connections_table.setRowCount(len(rows))
            for row, connection in enumerate(rows):
                host = str(connection.get("host") or connection.get("id") or "—")
                user_agent = str(connection.get("user_agent") or "—")
                values = [
                    str(row + 1),
                    host,
                    str(connection.get("port") or "—"),
                    self._transport_label(connection.get("transport")),
                    user_agent,
                    self._format_duration(connection.get("connected_for")),
                    self._format_duration(connection.get("idle_for")),
                    self._format_bytes(connection.get("bytes_sent")),
                ]
                for column, value in enumerate(values):
                    self.connections_table.setItem(row, column, QTableWidgetItem(value))
        finally:
            self.connections_table.setUpdatesEnabled(True)

    def _server_process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        process = self.sender()
        if process is self.server_process:
            self.server_process = None
            process.deleteLater()
        if self._shutting_down:
            return
        if exit_code == 0:
            self.statusBar().showMessage("Процесс StremDesk завершён", 7000)
        else:
            self.statusBar().showMessage(f"Процесс StremDesk завершён с кодом {exit_code}", 7000)

    def _server_process_error(self, error: QProcess.ProcessError) -> None:
        self.statusBar().showMessage(f"Ошибка запуска StremDesk: {error}", 7000)

    def _ensure_server_process(self) -> None:
        if self._shutting_down:
            return
        try:
            settings = parse_settings(["--config", str(self.config_path)])
        except ValueError as error:
            self.statusBar().showMessage(f"Не удалось запустить StremDesk: {error}", 7000)
            return
        if not settings.server_enabled and not settings.stream_enabled:
            return
        process = self.server_process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            return
        if _server_is_running(settings):
            self.statusBar().showMessage("HTTP-сервер уже запущен", 5000)
            return
        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setWorkingDirectory(str(Path(__file__).resolve().parent))
        process.setArguments(_server_arguments(self.arguments, self.config_path))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self._server_process_finished)
        process.errorOccurred.connect(self._server_process_error)
        self.server_process = process
        process.start()
        if not process.waitForStarted(3000):
            self.server_process = None
            process.deleteLater()
            self.statusBar().showMessage("Не удалось запустить процесс StremDesk", 7000)
            return
        self.statusBar().showMessage("StremDesk запускается…", 5000)

    def _browse_splash(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите заставку",
            "",
            "Изображения (*.png *.jpg *.jpeg *.webp *.gif *.bmp)",
        )
        if filename:
            self.splash_path.setText(filename)

    def _window_selected(self, index: int) -> None:
        if self._loading:
            return
        window_id = self.window_combo.itemData(index)
        if window_id:
            self.window_id.setText(str(window_id))

    def _pick_window_with_mouse(self) -> None:
        if not shutil.which("xdotool"):
            self.statusBar().showMessage("xdotool не установлен; введите X11 ID вручную", 7000)
            return
        self.pick_window.setEnabled(False)
        self.statusBar().showMessage("Передвиньте курсор на нужное окно и нажмите левую кнопку мыши", 5000)
        self.hide()
        QTimer.singleShot(5000, self._capture_mouse_window)

    def _capture_mouse_window(self) -> None:
        window_id = ""
        for line in _output_lines(["xdotool", "getmouselocation", "--shell"]):
            if line.startswith("WINDOW="):
                candidate = line.split("=", 1)[1].strip()
                if re.fullmatch(r"\d+", candidate):
                    window_id = candidate
                    break
        self.show()
        self.raise_()
        self.activateWindow()
        self.pick_window.setEnabled(True)
        if not window_id:
            self.statusBar().showMessage("Окно под курсором не определено", 7000)
            return
        title = " ".join(_output_lines(["xdotool", "getwindowname", window_id]))
        if not any(self.window_combo.itemData(index) == window_id for index in range(self.window_combo.count())):
            self.window_combo.addItem(f"{title or 'Окно'} [{window_id}]", window_id)
        self._set_combo(self.capture_mode, "window")
        self._set_combo(self.window_combo, window_id)
        self.window_id.setText(window_id)
        self.statusBar().showMessage(f"Выбрано окно {title or window_id}", 7000)

    def _populate_devices(self) -> None:
        self._loading = True
        self.window_combo.clear()
        self.window_combo.addItem("Окно не выбрано", "")
        for window_id, title in discover_windows():
            self.window_combo.addItem(f"{title} [{window_id}]", window_id)
        self.camera.clear()
        cameras = discover_cameras()
        self.camera.addItem("Выберите камеру", "")
        for camera in cameras:
            self.camera.addItem(camera, camera)
        if cameras:
            self.camera.setCurrentIndex(1)
        self._refresh_audio()
        self._loading = False

    def _refresh_audio(self) -> None:
        current = self.audio_source.currentText().strip() if hasattr(self, "audio_source") else ""
        self.audio_source.clear()
        for source in discover_audio_sources():
            self.audio_source.addItem(audio_source_label(source), source)
        if current:
            self._set_combo(self.audio_source, current)

    def _load(self) -> None:
        self._loading = True
        try:
            settings = parse_settings(["--config", str(self.config_path)])
        except ValueError as error:
            self._loading = False
            QMessageBox.critical(self, "Ошибка конфигурации", str(error))
            return
        self.server_enabled.setChecked(not settings.server_enabled)
        self.host.setText(settings.host)
        self.port.setValue(settings.port)
        self.token.setText(settings.token)
        self.open_browser.setChecked(settings.open_browser)
        self.ffmpeg.setText(settings.ffmpeg or "")
        self.stream_enabled.setChecked(not settings.stream_enabled)
        self.splash_path.setText(settings.splash_path)
        self._set_combo(self.ui_language, settings.ui_language)
        self._set_combo(self.capture_mode, settings.capture_mode)
        self.source.setText(settings.source)
        self._set_combo(self.video_codec, settings.video_codec)
        self._set_combo(self.encoder, settings.encoder)
        self.fps.setValue(settings.fps)
        self.bitrate.setValue(settings.bitrate)
        self.max_width.setValue(settings.max_width)
        self.max_height.setValue(settings.max_height)
        self.draw_mouse.setChecked(settings.draw_mouse)
        self._set_combo(self.window_combo, settings.window_id)
        self.window_id.setText(settings.window_id)
        self._set_combo(self.camera, settings.camera_device)
        self.audio_enabled.setChecked(settings.audio_enabled)
        self._set_combo(self.audio_source, settings.audio_source)
        self.audio_bitrate.setValue(settings.audio_bitrate)
        self._set_combo(self.audio_sample_rate, settings.audio_sample_rate)
        self._set_combo(self.audio_channels, settings.audio_channels)
        self._loading = False
        self._update_capture_controls()
        self.statusBar().showMessage(f"Загружено: {self.config_path}", 5000)

    @staticmethod
    def _set_combo(combo: QComboBox, value: object) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return
        if combo.isEditable():
            combo.setCurrentText(str(value))

    @staticmethod
    def _combo_value(combo: QComboBox) -> str:
        text = combo.currentText().strip()
        index = combo.currentIndex()
        data = combo.itemData(index)
        if data is not None and text == combo.itemText(index):
            return str(data)
        return text

    def _update_capture_controls(self, *_: object) -> None:
        mode = self.capture_mode.currentData()
        self.source.setEnabled(mode != "camera")
        self.window_combo.setEnabled(mode == "window")
        self.window_id.setEnabled(mode == "window")
        self.pick_window.setEnabled(mode == "window")
        self.camera.setEnabled(mode == "camera")
        self.draw_mouse.setEnabled(mode in {"screen", "window"})
        voice = mode == "voice"
        self._capture_form.setRowVisible(self._splash_row, voice)
        self._capture_form.setRowVisible(self._voice_hint_row, voice)

    def _schedule_save(self, *_: object) -> None:
        if self._loading or not self.auto_save.isChecked():
            return
        self._save_timer.start()

    def _collect_settings(self) -> Settings:
        capture_mode = str(self.capture_mode.currentData() or "screen")
        window_id = self.window_id.text().strip()
        if capture_mode == "window" and not window_id:
            raise ValueError("Выберите окно или введите X11 window ID")
        if capture_mode == "camera" and not self._combo_value(self.camera):
            raise ValueError("Выберите устройство камеры")
        source = self.source.text().strip() or "auto"
        arguments = [
            "--config",
            str(self.config_path),
            "--host",
            self.host.text().strip(),
            "--port",
            str(self.port.value()),
            "--fps",
            str(self.fps.value()),
            "--bitrate",
            str(self.bitrate.value()),
            "--encoder",
            str(self.encoder.currentData() or "auto"),
            "--max-width",
            str(self.max_width.value()),
            "--max-height",
            str(self.max_height.value()),
            "--source",
            source,
            "--capture-mode",
            capture_mode,
            "--window-id",
            window_id,
            "--camera-device",
            self._combo_value(self.camera),
            "--video-codec",
            str(self.video_codec.currentData() or "h264"),
            "--ffmpeg",
            self.ffmpeg.text().strip(),
            "--token",
            self.token.text().strip(),
            "--audio-source",
            self._combo_value(self.audio_source),
            "--audio-bitrate",
            str(self.audio_bitrate.value()),
            "--audio-sample-rate",
            str(self.audio_sample_rate.currentData() or 48000),
            "--audio-channels",
            str(self.audio_channels.currentData() or 2),
        ]
        arguments.append("--draw-mouse" if self.draw_mouse.isChecked() else "--no-draw-mouse")
        arguments.append("--audio-enabled" if self.audio_enabled.isChecked() else "--no-audio-enabled")
        arguments.append("--no-server-enabled" if self.server_enabled.isChecked() else "--server-enabled")
        arguments.append("--open-browser" if self.open_browser.isChecked() else "--no-browser")
        arguments.append("--no-stream-enabled" if self.stream_enabled.isChecked() else "--stream-enabled")
        arguments.extend(
            [
                "--splash-path",
                self.splash_path.text().strip(),
                "--ui-language",
                str(self.ui_language.currentData() or "ru"),
            ]
        )
        return parse_settings(arguments)

    def _save(self) -> None:
        if self._loading:
            return
        try:
            settings = self._collect_settings()
        except ValueError as error:
            self.statusBar().showMessage(f"Не сохранено: {error}", 7000)
            return
        payload = asdict(settings)
        payload["ffmpeg"] = settings.ffmpeg or ""
        temporary = self.config_path.with_name(f".{self.config_path.name}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.config_path)
        except OSError as error:
            self.statusBar().showMessage(f"Не удалось сохранить: {error}", 7000)
            return
        self.statusBar().showMessage(
            f"Сохранено и применено: сервер {'включён' if settings.server_enabled else 'выключен'}, "
            f"FFmpeg {'включён' if settings.stream_enabled else 'выключен'}, "
            f"захват {settings.capture_mode}, кодек {settings.video_codec}, "
            f"звук {'включён' if settings.audio_enabled else 'выключен'}, "
            f"язык {settings.ui_language}",
            7000,
        )
        QTimer.singleShot(0, self._ensure_server_process)
        QTimer.singleShot(0, self._refresh_connections)

    def closeEvent(self, event: object) -> None:
        self._status_timer.stop()
        if self.auto_save.isChecked() and not self._loading:
            self._save()
        self._stop_server_process()
        event.accept()

    def _stop_server_process(self) -> None:
        self._shutting_down = True
        self._status_timer.stop()
        if not self.stop_on_close.isChecked():
            self.statusBar().showMessage("StremDesk оставлен работать в фоне", 5000)
            return
        process = self.server_process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            self.statusBar().showMessage("Останавливаю StremDesk и стрим…", 5000)
            process.terminate()
            if not process.waitForFinished(6000):
                process.kill()
                process.waitForFinished(2000)
            self.server_process = None
            process.deleteLater()
            self.statusBar().showMessage("StremDesk и стрим остановлены", 5000)
            return
        self.server_process = None
        if self.stop_on_close.isChecked():
            self._stop_foreign_server()
        self.statusBar().showMessage("StremDesk и стрим остановлены", 5000)

    def _stop_foreign_server(self) -> None:
        pid = self._running_server_pid()
        if pid is None or pid == os.getpid():
            return
        try:
            command_line = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return
        if "stremdesk" not in command_line:
            return
        self.statusBar().showMessage(f"Останавливаю StremDesk (PID {pid})…", 5000)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if not Path(f"/proc/{pid}").exists():
                return
            time.sleep(0.2)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and Path(f"/proc/{pid}").exists():
            time.sleep(0.2)

    def _running_server_pid(self) -> int | None:
        try:
            with urllib.request.urlopen(self._status_url(), timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        pid = payload.get("server_pid") if isinstance(payload, dict) else None
        return pid if isinstance(pid, int) and pid > 0 else None


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    application = QApplication(arguments)
    application.setApplicationName("StremDesk Settings")
    window = SettingsWindow(arguments)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
