@echo off
setlocal

cd /d "%~dp0"

REM Run pack.ps1 (PowerShell handles all logic + Korean text safely)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack.ps1"

if errorlevel 1 (
    echo.
    echo [ERROR] Compression failed. See messages above.
)

echo.
pause
