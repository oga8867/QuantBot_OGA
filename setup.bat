@echo off
setlocal

cd /d "%~dp0"

echo ============================================================
echo            Quant Bot - One-Click Setup
echo ============================================================
echo.

REM --- Step 1: Check Python ---
echo [1/5] Checking Python...

set "PY_CMD="
python --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python"
    goto :PYTHON_OK
)

py --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py"
    goto :PYTHON_OK
)

echo       Python not found. Trying auto-install...
echo.

REM Try winget first
where winget >nul 2>&1
if errorlevel 1 goto :TRY_DIRECT_DOWNLOAD

echo       Installing via winget [3-5 minutes]...
winget install -e --id Python.Python.3.11 --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :TRY_DIRECT_DOWNLOAD

echo.
echo       Python installed. Please CLOSE this window and run setup.bat again.
echo       (PATH refresh required)
echo.
pause
exit /b 0

:TRY_DIRECT_DOWNLOAD
echo       Trying direct download from python.org...
set "PY_INSTALLER=%TEMP%\python-installer.exe"
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%PY_INSTALLER%'"
if not exist "%PY_INSTALLER%" goto :PY_INSTALL_FAILED

echo       Installer downloaded. Installing [3 minutes]...
"%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
del "%PY_INSTALLER%" >nul 2>&1
echo.
echo       Python installed. Please CLOSE this window and run setup.bat again.
echo.
pause
exit /b 0

:PY_INSTALL_FAILED
echo.
echo       [ERROR] Auto-install failed.
echo       Please install Python manually:
echo         1. Go to: https://www.python.org/downloads/
echo         2. Download Python 3.11.x
echo         3. CHECK "Add Python to PATH" during install
echo         4. Run setup.bat again
echo.
pause
exit /b 1

:PYTHON_OK
%PY_CMD% --version
echo.

REM --- Step 2: Create venv ---
echo [2/5] Creating virtual environment...
if exist "venv\Scripts\python.exe" (
    echo       venv already exists. Reusing.
    goto :VENV_OK
)

%PY_CMD% -m venv venv
if errorlevel 1 (
    echo       [ERROR] Failed to create venv.
    pause
    exit /b 1
)
echo       Done.

:VENV_OK
echo.

REM --- Step 3: Upgrade pip ---
echo [3/5] Upgrading pip...
venv\Scripts\python.exe -m pip install --upgrade pip --quiet
echo       Done.
echo.

REM --- Step 4: Install dependencies ---
echo [4/5] Installing packages [5-10 minutes, please wait]...
venv\Scripts\pip.exe install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo       [WARN] Some packages may have failed. Run for details:
    echo              venv\Scripts\pip.exe install -r requirements.txt
) else (
    echo       Done.
)
echo.

REM --- Step 5: Setup config + folders ---
echo [5/5] Setting up config files...

if exist ".env" goto :ENV_EXISTS

if exist ".env.example" (
    copy ".env.example" ".env" >nul
    echo       .env created from template.
    goto :ENV_DONE
)

REM Fallback: create minimal .env
> .env echo # Quant Bot Environment Variables
>> .env echo # Enter API keys via Dashboard Settings tab, or edit here directly
>> .env echo.
>> .env echo KIS_APP_KEY=
>> .env echo KIS_APP_SECRET=
>> .env echo KIS_ACCOUNT=
>> .env echo KIS_PAPER=true
>> .env echo.
>> .env echo TELEGRAM_TOKEN=
>> .env echo TELEGRAM_CHAT_ID=
>> .env echo DISCORD_WEBHOOK_URL=
echo       .env created.
goto :ENV_DONE

:ENV_EXISTS
echo       .env already exists. Keeping it.

:ENV_DONE
if not exist "data" mkdir data
if not exist "reports" mkdir reports
if not exist "logs" mkdir logs

REM Check if API_KEYS.txt exists - first-time setup
set "FIRST_TIME_SETUP=0"
if not exist "API_KEYS.txt" (
    set "FIRST_TIME_SETUP=1"
    REM Copy template if available
    if exist "API_KEYS.txt.example" (
        copy "API_KEYS.txt.example" "API_KEYS.txt" >nul
        echo       API_KEYS.txt created from template.
    )
)

echo.
echo ============================================================
echo                    Setup Complete!
echo ============================================================
echo.
echo  Dashboard URL: http://localhost:5000
echo.

if "%FIRST_TIME_SETUP%"=="1" (
    echo  [!] API_KEYS.txt not found. Please enter your API keys.
    echo      File will open in Notepad in 3 seconds.
    echo.
    echo      Steps:
    echo       1. Enter your keys inside the '' single quotes
    echo       2. Save Ctrl+S and close Notepad
    echo       3. Restart bot start.bat to load the keys
    echo.
    echo ============================================================
    echo.
    timeout /t 3 /nobreak >nul
    start "" notepad "API_KEYS.txt"
) else (
    echo  Tips:
    echo   - Edit API keys: open API_KEYS.txt with Notepad
    echo   - Stop bot: Close this window or press Ctrl-C
    echo   - Browser will open in 5 seconds.
    echo.
    echo ============================================================
    echo.
)

REM Open browser after 5 seconds (separate cmd, non-blocking)
start "" cmd /c "timeout /t 5 /nobreak >nul && start http://localhost:5000"

REM Run dashboard (blocks here until stopped)
venv\Scripts\python.exe dashboard\app.py

echo.
echo Dashboard stopped.
pause
