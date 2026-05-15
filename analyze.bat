@echo off
REM =============================================================================
REM analyze.bat - Quant Bot Analysis Runner
REM =============================================================================
REM Double-click: prompts for symbol interactively
REM Command line: analyze.bat AAPL --capital 50000000
REM =============================================================================

cd /d "%~dp0"

REM If no arguments given, ask user for input
if "%~1"=="" (
    echo ========================================
    echo   Quant Bot - Stock Analysis
    echo ========================================
    echo.
    echo   US stocks: AAPL, MSFT, GOOGL, NVDA, TSLA ...
    echo   KR stocks: 005930.KS, 035720.KQ ...
    echo.
    set /p SYMBOLS="Enter symbol(s): "
) else (
    set SYMBOLS=%*
)

if "%SYMBOLS%"=="" (
    echo [ERROR] No symbol entered.
    pause
    exit /b 1
)

REM Use venv Python first, fallback to system Python
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe main.py %SYMBOLS%
) else (
    echo [WARNING] No venv found. Run install.bat first.
    python main.py %SYMBOLS%
)

pause
