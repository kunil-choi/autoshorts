# Downloads a static Windows ffmpeg build and extracts ffmpeg.exe/ffprobe.exe
# into installer\ffmpeg_bin\, for build.bat to bundle next to the built exe.
#
# If this URL 404s (BtbN occasionally renames release assets), grab the
# latest "ffmpeg-master-latest-win64-gpl.zip" yourself from
# https://github.com/BtbN/FFmpeg-Builds/releases and drop ffmpeg.exe +
# ffprobe.exe directly into installer\ffmpeg_bin\ instead of running this.
#
# Kept plain-ASCII on purpose - Windows PowerShell 5.1 guesses a .ps1
# file's encoding from the system's legacy code page when there's no BOM,
# so any non-ASCII text here risks the same kind of corruption a non-UTF-8
# read of a UTF-8 file causes elsewhere.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$binDir = Join-Path $scriptDir "ffmpeg_bin"

if ((Test-Path (Join-Path $binDir "ffmpeg.exe")) -and (Test-Path (Join-Path $binDir "ffprobe.exe"))) {
    Write-Host "ffmpeg.exe/ffprobe.exe already present in ffmpeg_bin - skipping download."
    exit 0
}

New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$zipPath = Join-Path $scriptDir "ffmpeg-download.zip"
$url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"

Write-Host "Downloading ffmpeg from: $url"
try {
    Invoke-WebRequest -Uri $url -OutFile $zipPath
} catch {
    Write-Error "Failed to download ffmpeg. The URL above may no longer be valid - get the latest win64-gpl zip yourself from https://github.com/BtbN/FFmpeg-Builds/releases and put ffmpeg.exe/ffprobe.exe into $binDir"
    exit 1
}

$extractDir = Join-Path $scriptDir "ffmpeg-extract-tmp"
if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
Expand-Archive -Path $zipPath -DestinationPath $extractDir

$ffmpegExe = Get-ChildItem -Path $extractDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
$ffprobeExe = Get-ChildItem -Path $extractDir -Recurse -Filter "ffprobe.exe" | Select-Object -First 1

if (-not $ffmpegExe -or -not $ffprobeExe) {
    Write-Error "Could not find ffmpeg.exe/ffprobe.exe inside the downloaded archive - check $extractDir manually."
    exit 1
}

Copy-Item $ffmpegExe.FullName (Join-Path $binDir "ffmpeg.exe") -Force
Copy-Item $ffprobeExe.FullName (Join-Path $binDir "ffprobe.exe") -Force

Remove-Item $zipPath -Force
Remove-Item -Recurse -Force $extractDir

Write-Host "Done: ffmpeg.exe / ffprobe.exe ready in $binDir"
