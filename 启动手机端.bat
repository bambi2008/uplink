@echo off
setlocal
title Uplink Mobile
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "BASE_CMD="
set "BASE_ARGS="
if not exist "%VENV_PY%" (
  for /f "delims=" %%I in ('where py 2^>nul') do if not defined BASE_CMD set "BASE_CMD=%%I"
  if defined BASE_CMD set "BASE_ARGS=-3"
  if not defined BASE_CMD for /f "delims=" %%I in ('where python 2^>nul') do if not defined BASE_CMD set "BASE_CMD=%%I"
)
if not exist "%VENV_PY%" if not defined BASE_CMD (
  echo [Uplink] Python 3.9+ was not found.
  pause
  exit /b 1
)
if not exist "%VENV_PY%" (
  "%BASE_CMD%" %BASE_ARGS% -m venv ".venv"
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

"%VENV_PY%" -c "import aiohttp,segno" >nul 2>&1
if errorlevel 1 (
  echo [Uplink] Installing phone support...
  "%VENV_PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

echo.
echo [Uplink] Temporary phone mode uses a public Cloudflare HTTPS address.
echo [Uplink] Access is protected by a private pairing token, but call traffic is relayed by Cloudflare.
choice /C YN /N /M "Continue? [Y/N] "
if errorlevel 2 exit /b 0

if not exist ".tools\cloudflared.exe" (
  echo [Uplink] Downloading the HTTPS phone gateway...
  if not exist ".tools" mkdir ".tools"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile '.tools\cloudflared.exe'"
  if errorlevel 1 (
    echo [Uplink] Download failed. Check the network and retry.
    pause
    exit /b 1
  )
)

"%VENV_PY%" mobile_launcher.py
if errorlevel 1 pause
