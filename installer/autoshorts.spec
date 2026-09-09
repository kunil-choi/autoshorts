# PyInstaller spec for a portable, onedir build of autoshorts.
#
# Build with: pyinstaller installer/autoshorts.spec --distpath dist --workpath build
# (installer/build.bat does this, plus fetching ffmpeg first - run that
# instead of calling pyinstaller directly, unless you're just iterating on
# this spec file itself.)
#
# onedir (not onefile): a single .exe is nicer to hand out, but onefile
# re-extracts every bundled binary (ffmpeg.exe, faster-whisper's ctranslate2
# native libs, opencv) into a fresh temp directory on *every* launch, which
# is slow and fragile for a bundle this size. The dist/autoshorts/ folder
# this produces (with autoshorts.exe inside) is what actually gets zipped
# and handed to other departments - "double-click autoshorts.exe" is still
# a one-click experience even though it's technically inside a folder.

import sys
from pathlib import Path

block_cipher = None
REPO_ROOT = Path(SPECPATH).resolve().parent

# .env.example and ffmpeg_bin/ are deliberately NOT listed here - PyInstaller
# >= 6's onedir mode buries `datas` inside _internal/, but those two need to
# sit directly next to the built .exe where a non-technical worker will
# actually look for them (and where installer/launcher.py looks for them at
# runtime). installer/build.bat copies them in as a plain post-build step.
datas = [
    (str(REPO_ROOT / "webui" / "templates"), "templates"),
    (str(REPO_ROOT / "webui" / "static"), "static"),
]

# libraries whose submodules PyInstaller's static analysis is known to miss
# (dynamic/plugin-style imports it can't see just by reading the code)
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "anthropic",
    "faster_whisper",
    "webvtt",
    "cv2",
    "PIL",
    "yt_dlp",
    "multipart",
]

a = Analysis(
    [str(REPO_ROOT / "installer" / "launcher.py")],
    pathex=[str(REPO_ROOT / "src"), str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="autoshorts",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="autoshorts",
)
