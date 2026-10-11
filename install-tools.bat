@echo off
setlocal EnableExtensions
title Install FFmpeg and MKVToolNix
echo.
echo   Installs the two free tools SubScrub uses, via winget:
echo     - FFmpeg      (required)  reads and writes video files
echo     - MKVToolNix  (optional)  the most faithful way to rewrite MKV files
echo.
where winget >nul 2>&1
if errorlevel 1 (
  echo   winget isn't available on this PC. Get the tools here instead:
  echo     FFmpeg:     https://www.gyan.dev/ffmpeg/builds/
  echo     MKVToolNix: https://mkvtoolnix.download/downloads.html
  echo   then point SubScrub at them from the Tools window.
  pause
  exit /b 1
)
winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
winget install -e --id MoritzBunkus.MKVToolNix --accept-source-agreements --accept-package-agreements
echo.
echo   All set. If SubScrub is open, click the tools pill in its header and hit Re-scan.
pause
