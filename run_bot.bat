@echo off
REM =============================================================================
REM run_bot.bat - Quant Bot Auto-Trading Mode
REM =============================================================================
REM Starts scheduler for automated analysis and trading.
REM Press Ctrl+C to stop.
REM
REM Usage:
REM   run_bot.bat                    (default: paper trading)
REM   run_bot.bat --live             (LIVE trading - caution!)
REM   run_bot.bat --capital 50000000 (set capital)
REM =============================================================================

cd /d "%~dp0"

echo ========================================
echo   Quant Bot - Auto Trading Mode
echo ========================================
echo.
echo  [!] Press Ctrl+C to stop
echo.

REM Use venv Python first
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe run_bot.py %*
) else (
    echo [WARNING] No venv found. Run install.bat first.
    python run_bot.py %*
)

pause
