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
if exist "dist\autoshorts" rmdir /s /q "dist\autoshorts"
pyinstaller installer\autoshorts.spec --distpath dist --workpath build --noconfirm
if errorlevel 1 goto :error

echo === Step 5/5: copying ffmpeg and .env.example next to the exe ===
xcopy installer\ffmpeg_bin dist\autoshorts\ffmpeg_bin /E /I /Y
copy /Y webui\.env.example dist\autoshorts\.env.example

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
