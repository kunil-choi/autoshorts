@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0\.."

echo === Step 0/5: checking Python ===
python --version >nul 2>nul
if errorlevel 1 (
    echo Python was not found - or "python" is just the Windows Store shortcut.
    echo Install real Python 3.11+ from https://www.python.org/downloads/
    echo and check "Add python.exe to PATH" during setup, then close this
    echo window, open a new one, and run this script again.
    pause
    exit /b 1
)

echo === Step 1/5: setting up virtual environment ===
if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 goto :error
)
call .venv\Scripts\activate.bat

echo === Step 2/5: installing packages (requirements.txt + pyinstaller) ===
pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo === Step 3/5: downloading ffmpeg ===
powershell -ExecutionPolicy Bypass -File "installer\download_ffmpeg.ps1"
if errorlevel 1 goto :error

echo === Step 4/5: building with PyInstaller ===
REM save the previous build's .env (API key etc.) before wiping the dist
REM folder below - it lives inside dist\autoshorts, not in the source tree,
REM so a rebuild would otherwise silently delete it and force re-entering
REM the API key every single time.
set "ENV_BACKUP=%TEMP%\autoshorts_env_backup_%RANDOM%.env"
if exist "dist\autoshorts\.env" copy /Y "dist\autoshorts\.env" "%ENV_BACKUP%" >nul

REM clear both the output AND PyInstaller's intermediate cache - a fresh
REM zip re-download can give files timestamps that don't reliably increase
REM from the last build, which can fool PyInstaller's mtime-based cache
REM into reusing stale compiled code from the "build" folder even though
REM the source actually changed. Only clearing "dist" (as this used to)
REM isn't enough to guarantee a truly clean build.
if exist "dist\autoshorts" rmdir /s /q "dist\autoshorts"
if exist "build" rmdir /s /q "build"
pyinstaller installer\autoshorts.spec --distpath dist --workpath build --noconfirm
if errorlevel 1 goto :error

echo === Step 5/5: copying ffmpeg and .env.example next to the exe ===
xcopy installer\ffmpeg_bin dist\autoshorts\ffmpeg_bin /E /I /Y
copy /Y webui\.env.example dist\autoshorts\.env.example

if exist "%ENV_BACKUP%" (
    copy /Y "%ENV_BACKUP%" "dist\autoshorts\.env" >nul
    del "%ENV_BACKUP%"
    echo Existing .env (API key etc.) carried over from the previous build.
)

echo.
echo ============================================================
echo Done! dist\autoshorts\ is the finished, distributable build.
echo  - To run it: double-click dist\autoshorts\autoshorts.exe
echo  - To share it: zip the whole dist\autoshorts\ folder and send it -
echo    no Python or ffmpeg install needed on the other machine.
echo ============================================================
pause
exit /b 0

:error
echo.
echo Build failed. Check the log above for the error.
pause
exit /b 1
