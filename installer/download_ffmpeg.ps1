# Downloads a static Windows ffmpeg build and extracts ffmpeg.exe/ffprobe.exe
# into installer\ffmpeg_bin\, for build.bat to bundle next to the built exe.
#
# If this URL 404s (BtbN occasionally renames release assets), grab the
# latest "ffmpeg-master-latest-win64-gpl.zip" yourself from
# https://github.com/BtbN/FFmpeg-Builds/releases and drop ffmpeg.exe +
# ffprobe.exe directly into installer\ffmpeg_bin\ instead of running this.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$binDir = Join-Path $scriptDir "ffmpeg_bin"

if ((Test-Path (Join-Path $binDir "ffmpeg.exe")) -and (Test-Path (Join-Path $binDir "ffprobe.exe"))) {
    Write-Host "ffmpeg_bin에 ffmpeg.exe/ffprobe.exe가 이미 있어 다운로드를 건너뜁니다."
    exit 0
}

New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$zipPath = Join-Path $scriptDir "ffmpeg-download.zip"
$url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"

Write-Host "ffmpeg 다운로드 중: $url"
try {
    Invoke-WebRequest -Uri $url -OutFile $zipPath
} catch {
    Write-Error "ffmpeg 다운로드 실패. 위 URL이 더 이상 유효하지 않을 수 있습니다 - https://github.com/BtbN/FFmpeg-Builds/releases 에서 최신 win64-gpl zip을 직접 받아 ffmpeg.exe/ffprobe.exe를 $binDir 에 넣어주세요."
    exit 1
}

$extractDir = Join-Path $scriptDir "ffmpeg-extract-tmp"
if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
Expand-Archive -Path $zipPath -DestinationPath $extractDir

$ffmpegExe = Get-ChildItem -Path $extractDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
$ffprobeExe = Get-ChildItem -Path $extractDir -Recurse -Filter "ffprobe.exe" | Select-Object -First 1

if (-not $ffmpegExe -or -not $ffprobeExe) {
    Write-Error "다운로드한 압축 파일 안에서 ffmpeg.exe/ffprobe.exe를 찾지 못했습니다 - $extractDir 안을 직접 확인해주세요."
    exit 1
}

Copy-Item $ffmpegExe.FullName (Join-Path $binDir "ffmpeg.exe") -Force
Copy-Item $ffprobeExe.FullName (Join-Path $binDir "ffprobe.exe") -Force

Remove-Item $zipPath -Force
Remove-Item -Recurse -Force $extractDir

Write-Host "완료: $binDir 에 ffmpeg.exe / ffprobe.exe 준비됨"
