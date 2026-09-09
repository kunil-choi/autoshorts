"""Entry point for the frozen (PyInstaller) build - double-clicking the
built .exe runs this. Not used in normal dev mode (`uvicorn webui.server:app`).

Responsibilities the plain dev command doesn't need to worry about:
  - put the bundled ffmpeg/ffprobe on PATH (see installer/build.bat, which
    downloads them into ffmpeg_bin/ next to the built exe)
  - default AUTOSHORTS_DISPLAY_FONT to Windows' built-in Malgun Gothic Bold
    if nothing else is set - Windows ships this on virtually every install,
    so bundling a font file (and its licensing questions) isn't necessary
  - scaffold a first-run .env from .env.example so a non-technical worker
    has an obvious file to open and paste their API key into
  - open the browser automatically, since there's no terminal-savvy user
    expected to type the localhost URL themselves
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path

PORT = 8787


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _setup_ffmpeg_path(app_dir: Path) -> None:
    ffmpeg_dir = app_dir / "ffmpeg_bin"
    if ffmpeg_dir.is_dir():
        os.environ["PATH"] = str(ffmpeg_dir) + os.pathsep + os.environ.get("PATH", "")


def _setup_default_font() -> None:
    if os.environ.get("AUTOSHORTS_DISPLAY_FONT"):
        return
    if platform.system() == "Windows":
        malgun = Path(r"C:\Windows\Fonts\malgunbd.ttf")
        if malgun.is_file():
            os.environ["AUTOSHORTS_DISPLAY_FONT"] = str(malgun).replace("\\", "/")


def _scaffold_env(app_dir: Path) -> None:
    env_path = app_dir / ".env"
    example_path = app_dir / ".env.example"
    if not env_path.exists() and example_path.exists():
        shutil.copyfile(example_path, env_path)
        print(f"[autoshorts] {env_path} 파일을 새로 만들었습니다.")
        print("[autoshorts] 이 파일을 열어 ANTHROPIC_API_KEY를 채운 뒤 다시 실행해주세요.")


def _open_browser_when_ready() -> None:
    time.sleep(2.0)
    webbrowser.open(f"http://127.0.0.1:{PORT}")


def main() -> None:
    app_dir = _app_dir()
    os.chdir(app_dir)  # so work/ (job files) lands next to the exe, not wherever it was launched from

    _setup_ffmpeg_path(app_dir)
    _setup_default_font()
    _scaffold_env(app_dir)

    import uvicorn

    from webui.server import app as fastapi_app

    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    print("autoshorts 서버를 시작합니다...")
    print(f"잠시 후 브라우저가 자동으로 열립니다 (http://127.0.0.1:{PORT}).")
    print("이 창을 닫으면 서버가 종료됩니다.")
    uvicorn.run(fastapi_app, host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
