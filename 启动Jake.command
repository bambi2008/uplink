#!/bin/bash
set -e
cd "$(dirname "$0")"
if ! python3 -c "import aiohttp" >/dev/null 2>&1; then
  echo "Installing dependencies for the first run..."
  python3 -m pip install --user -r requirements.txt 2>/dev/null || \
    python3 -m pip install --break-system-packages -r requirements.txt
fi
exec python3 launcher.py
