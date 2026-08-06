@echo off
setlocal
title Uplink
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "BASE_CMD="
set "BASE_ARGS="
if not exist "%VENV_PY%" (
  for /f "delims=" %%I in ('where py 2^>nul') do if not defined BASE_CMD set "BASE_CMD=%%I"
  if defined BASE_CMD set "BASE_ARGS=-3"
  if not defined BASE_CMD (
    for /f "delims=" %%I in ('where python 2^>nul') do if not defined BASE_CMD set "BASE_CMD=%%I"
  )
  if not defined BASE_CMD if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    set "BASE_CMD=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
  )
)
if not exist "%VENV_PY%" if not defined BASE_CMD (
  echo [Uplink] Python 3.9+ was not found. Please install Python first.
  pause
  exit /b 1
)
if not exist "%VENV_PY%" (
  echo [Uplink] Creating an isolated Python environment...
  "%BASE_CMD%" %BASE_ARGS% -m venv ".venv"
  if errorlevel 1 (
    echo [Uplink] Could not create the local Python environment.
    pause
    exit /b 1
  )
)

"%VENV_PY%" -c "import aiohttp" >nul 2>&1
if errorlevel 1 (
  echo [Uplink] Installing dependencies for the first run...
  "%VENV_PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [Uplink] Dependency installation failed. Check the network and retry.
    pause
    exit /b 1
  )
)

"%VENV_PY%" launcher.py
if errorlevel 1 pause
