@echo off
setlocal
cd /d "%~dp0"

if exist "runtime\python\python.exe" (
    "runtime\python\python.exe" "stremdesk.py" %*
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python не найден. Запусти build-portable.ps1 или установи Python 3.12+.
        pause
        exit /b 1
    )
    python "stremdesk.py" %*
)

endlocal
