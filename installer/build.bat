@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0\.."

echo === 0/5: Python 확인 ===
where python >nul 2>nul
if errorlevel 1 (
    echo Python이 PATH에 없습니다. https://www.python.org/downloads/ 에서 3.11 이상을 설치하고
    echo 설치 시 "Add python.exe to PATH"를 체크한 뒤 다시 실행해주세요.
    exit /b 1
)

echo === 1/5: 가상환경 준비 ===
if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 goto :error
)
call .venv\Scripts\activate.bat

echo === 2/5: 패키지 설치 (requirements.txt + pyinstaller) ===
pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo === 3/5: ffmpeg 다운로드 ===
powershell -ExecutionPolicy Bypass -File "installer\download_ffmpeg.ps1"
if errorlevel 1 goto :error

echo === 4/5: PyInstaller 빌드 ===
if exist "dist\autoshorts" rmdir /s /q "dist\autoshorts"
pyinstaller installer\autoshorts.spec --distpath dist --workpath build --noconfirm
if errorlevel 1 goto :error

echo === 5/5: ffmpeg / .env.example 를 exe 옆으로 복사 ===
xcopy installer\ffmpeg_bin dist\autoshorts\ffmpeg_bin /E /I /Y
copy /Y webui\.env.example dist\autoshorts\.env.example

echo.
echo ============================================================
echo 완료! dist\autoshorts\ 폴더가 배포 가능한 결과물입니다.
echo  - 실행: dist\autoshorts\autoshorts.exe 더블클릭
echo  - 다른 부서에 배포: dist\autoshorts\ 폴더 전체를 zip으로 압축해서 전달
echo    (받는 쪽은 Python/ffmpeg 설치 없이 autoshorts.exe만 실행하면 됩니다)
echo ============================================================
exit /b 0

:error
echo.
echo 빌드 중 오류가 발생했습니다. 위 로그를 확인해주세요.
exit /b 1
