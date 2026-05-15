@echo off
REM =============================================================================
REM dashboard.bat - Quant Bot Web Dashboard
REM =============================================================================
REM Opens the dashboard in your browser at http://localhost:5000
REM Press Ctrl+C to stop the server.
REM =============================================================================

cd /d "%~dp0"

REM Check if flask-socketio is installed
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe -c "import flask_socketio" >nul 2>&1
    if errorlevel 1 (
        echo [SETUP] Installing dashboard dependencies...
        venv\Scripts\pip.exe install flask flask-socketio --quiet
    )
    echo.
    echo  Opening dashboard at http://localhost:5000
    echo  Press Ctrl+C to stop.
    echo.
    start http://localhost:5000
    venv\Scripts\python.exe dashboard\app.py
) else (
    echo [WARNING] No venv found. Run install.bat first.
    pause
)
