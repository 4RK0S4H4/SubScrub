@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Building SubScrub

echo.
echo   SubScrub - build
echo   ================
echo   This makes dist\SubScrub.exe. It takes a few minutes the first time.
echo.

rem ---------------------------------------------------------------- find Python
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY goto :nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 goto :nopython
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo   Using %%v

rem ---------------------------------------------------------------- venv + deps
if not exist ".venv\Scripts\python.exe" (
  echo   Creating a private Python environment in .venv ...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)
set "VPY=%CD%\.venv\Scripts\python.exe"
echo   Installing PySide6, Send2Trash and PyInstaller ...
"%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------- self-test
echo   Running the cleaner self-test ...
"%VPY%" -m tests.test_cleaner >nul && "%VPY%" -m tests.test_library >nul && "%VPY%" -m tests.test_batch >nul && "%VPY%" -m tests.test_swap >nul && "%VPY%" -m tests.test_naming >nul
if errorlevel 1 (
  echo   The self-test failed. Run  .venv\Scripts\python -m tests.test_cleaner  ^(and tests.test_batch^) to see why.
  goto :fail
)

rem ---------------------------------------------------------------- build
echo   Building the app ...
"%VPY%" -m PyInstaller --noconfirm --clean --log-level WARN --onefile --windowed ^
  --name SubScrub --icon assets\subscrub.ico --hidden-import PySide6.QtSvg --exclude-module tkinter run.py
if errorlevel 1 goto :fail
if not exist "dist\SubScrub.exe" goto :fail

echo.
echo   Done:  %CD%\dist\SubScrub.exe
echo.

rem ---------------------------------------------------------------- tools check
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo   Note: FFmpeg isn't on your PATH yet. Run install-tools.bat, or use the
  echo   "Set up FFmpeg" button inside the app.
  echo.
)

choice /C YN /M "  Add a SubScrub shortcut to your desktop"
if errorlevel 2 goto :open
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\SubScrub.lnk');" ^
  "$s.TargetPath='%CD%\dist\SubScrub.exe'; $s.WorkingDirectory='%CD%\dist'; $s.IconLocation='%CD%\dist\SubScrub.exe,0'; $s.Save()"
echo   Shortcut added.

:open
start "" explorer "%CD%\dist"
echo.
pause
exit /b 0

:nopython
echo   Python 3.9 or newer wasn't found.
where winget >nul 2>&1
if errorlevel 1 (
  echo   Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
  echo   then run build.bat again.
  pause
  exit /b 1
)
choice /C YN /M "  Install Python 3.12 now with winget"
if errorlevel 2 exit /b 1
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
echo.
echo   Python is installed. Close this window and run build.bat again
echo   (a new window picks up the updated PATH).
pause
exit /b 0

:fail
echo.
echo   Build failed - scroll up for the error. Nothing outside this folder was changed.
pause
exit /b 1
