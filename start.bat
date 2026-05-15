@echo off
setlocal

cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found.
    echo         Please run setup.bat first.
    echo.
    pause
    exit /b 1
)

echo ============================================================
echo            Quant Bot Dashboard Starting
echo ============================================================
echo.
echo  URL: http://localhost:5000
echo  Stop: Close this window or press Ctrl-C
echo.
echo ============================================================
echo.

REM Open browser after 3 seconds
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:5000"

REM Run dashboard
venv\Scripts\python.exe dashboard\app.py

echo.
echo Dashboard stopped.
pause
