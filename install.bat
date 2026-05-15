@echo off
REM =============================================================================
REM install.bat - Quant Bot Installer
REM =============================================================================
REM Creates venv in project folder and installs dependencies.
REM Must use CRLF line endings (LF only will cause instant close!)
REM =============================================================================

cd /d "%~dp0"

echo ========================================
echo   Quant Bot - Installing...
echo ========================================
echo.

REM Check Python exists
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Please install from https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Create venv
if not exist "venv" (
    echo [1/3] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
    echo       Done!
) else (
    echo [1/3] venv already exists. Skipping.
)

REM Upgrade pip
echo [2/3] Upgrading pip...
venv\Scripts\python.exe -m pip install --upgrade pip --quiet

REM Install packages
echo [3/3] Installing packages...
venv\Scripts\pip.exe install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Package install failed. Check requirements.txt
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Installation Complete!
echo ========================================
echo.
echo Usage:
echo   analyze.bat AAPL
echo   analyze.bat 005930.KS --capital 50000000
echo.
pause
