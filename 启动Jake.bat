@echo off
setlocal
title Uplink
cd /d "%~dp0"

set "PY="
where py >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [Uplink] Python 3.9+ was not found. Please install Python first.
  pause
  exit /b 1
)

%PY% -c "import aiohttp" >nul 2>&1
if errorlevel 1 (
  echo [Uplink] Installing dependencies for the first run...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [Uplink] Dependency installation failed. Check the network and retry.
    pause
    exit /b 1
  )
)

%PY% launcher.py
if errorlevel 1 pause
