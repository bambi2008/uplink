@echo off
setlocal
title Uplink Mobile Stable
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo [Uplink] Please run 启动Jake.bat once before setting up the phone version.
  pause
  exit /b 1
)

"%VENV_PY%" -c "import aiohttp,segno" >nul 2>&1
if errorlevel 1 (
  "%VENV_PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

where tailscale >nul 2>&1
if errorlevel 1 if not exist "%ProgramFiles%\Tailscale\tailscale.exe" (
  echo [Uplink] Tailscale is required for a private fixed phone address.
  echo [Uplink] Install it on this PC, iPhone, and Android, then sign in with the same account.
  start "" "https://tailscale.com/download"
  pause
  exit /b 1
)

"%VENV_PY%" mobile_launcher.py --gateway tailscale
if errorlevel 1 pause
