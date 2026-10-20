#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${STREMDESK_PYTHON:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    printf 'Python не найден: %s\n' "$PYTHON_BIN" >&2
    exit 1
fi

if ! "$PYTHON_BIN" -c 'from PyQt6.QtWidgets import QApplication' >/dev/null 2>&1; then
    printf 'PyQt6 не найден. Установите пакет python3-pyqt6.\n' >&2
    exit 1
fi

export DISPLAY="${DISPLAY:-:0.0}"
exec "$PYTHON_BIN" "$ROOT_DIR/stremdesk_settings.py" "$@"
