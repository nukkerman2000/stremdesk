# StremDesk

Локальный стример рабочего стола (и не только) на Python + FFmpeg с веб-просмотром в реальном времени.

StremDesk захватывает экран, окно, камеру или просто аудио («voice»), кодирует поток аппаратным или программным энкодером и раздаёт его клиентам по HTTP/MSE прямо в браузере — без облака, без аккаунтов, без задержек промежуточных сервисов.

## Возможности

- **Захват**: весь экран, отдельное окно (X11), камера (V4L2 / DirectShow) или только голос со статичной splash-картинкой.
- **Энкодеры**: NVENC (NVIDIA), QSV (Intel), AMF (AMD) с автоматическим фолбэком на libx264 / libx265.
- **Кодеки**: H.264 и HEVC (H.265).
- **Аудио**: системный вывод (PulseAudio/ALSA monitor) или микрофон, настраиваемый битрейт/частота/каналы.
- **Веб-интерфейс**: встроенная HTML-страница просмотра + эндпоинт `/vlc.mp4` для VLC и других плееров.
- **Безопасность**: опциональный токен-доступ (`?token=...`).
- **Hot-reload конфига**: редактируешь `config.json` — настройки применяются на лету, без перезапуска.
- **GUI-настройки**: PyQt6-панель `stremdesk_settings.py` для управления всеми параметрами с мыши.
- **Мультиязычность**: интерфейс на русском, украинском, английском и белорусском.
- **Портативная сборка**: `build-portable.ps1` собирает самодостаточный дистрибутив для Windows (Python + FFmpeg внутри).

## Быстрый старт

### Linux

```bash
sudo apt install ffmpeg python3
./start-stremdesk.sh
```

### Windows

```powershell
# портативная сборка (тянет Python embeddable + FFmpeg)
.\build-portable.ps1
# запуск
.\Start-StremDesk.bat
```

После запуска открой в браузере `http://127.0.0.1:8080/` (или замени на IP машины для доступа из сети).

### GUI-настройки

```bash
python3 stremdesk_settings.py
```

## Конфигурация

Все параметры хранятся в `config.json` рядом с проектом:

```json
{
  "host": "0.0.0.0",
  "port": 8080,
  "fps": 30,
  "bitrate": 6000,
  "encoder": "nvenc",
  "video_codec": "h264",
  "capture_mode": "screen",
  "audio_enabled": true,
  "token": "",
  "ui_language": "ru"
}
```

| Параметр | Описание |
| --- | --- |
| `host` / `port` | Адрес HTTP-сервера (`0.0.0.0` — все интерфейсы) |
| `fps` / `bitrate` | Частота кадров и видеобитрейт (кбит/с) |
| `encoder` | `auto`, `nvenc`, `qsv`, `amf`, `x264`, `x265` |
| `video_codec` | `h264` или `hevc` |
| `capture_mode` | `screen`, `window`, `camera`, `voice` |
| `window_id` | X11 ID окна для захвата конкретного окна |
| `camera_device` | Путь к устройству камеры (`/dev/video0`) |
| `audio_source` | Источник звука (PulseAudio sink monitor) |
| `token` | Секрет для доступа к веб-интерфейсу (пусто = без токена) |
| `splash_path` | Картинка для режима `voice` |
| `ui_language` | `ru`, `uk`, `en`, `be` |

## Архитектура

```
stremdesk.py            точка входа
desktop_streamer/
├── app.py              оркестрация: сервер, стример, watcher конфига
├── config.py           парсинг и валидация настроек
├── stream.py           запуск/управление FFmpeg, авто-выбор энкодера
├── http_server.py      HTTP-обработчик: веб-интерфейс, /vlc.mp4, /healthz
├── hub.py              StreamHub: подписки клиентов, рассылка чанков
└── web/                статика веб-интерфейса (MSE-плеер)
stremdesk_settings.py   PyQt6 GUI для настроек
tests/                  pytest-тесты (config, http, hub, stream)
```

- **StreamHub** — потоковая шина: FFmpeg пишет чанки, Hub рассылает их всем подключённым клиентам с историей последних кадров для быстрого reconnect.
- **ConfigWatcher** — следит за `config.json` и применяет изменения без перезапуска (сервер перезапускается только при смене host/port).
- **HTTP-сервер** — отдаёт веб-плеер (MPEG-TS → MSE), поток для VLC и JSON-статус `/healthz`.

## Тесты

```bash
python3 -m pytest
```

## Требования

- Python 3.12+
- FFmpeg (в PATH или в `runtime/ffmpeg/`)
- Linux: X11 (x11grab), PulseAudio/PipeWire для аудио
- Windows: любой поддерживаемый захват (ddagrab / gdigrab / dshow)
- Для GUI-настроек: PyQt6

## Лиценсия

См. LICENSE в репозитории.
