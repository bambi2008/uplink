#!/bin/bash
# macOS one-click launcher
cd "$(dirname "$0")"
echo "Preparing Uplink (first run installs aiohttp; network required)..."
python3 -m pip install --quiet --user aiohttp 2>/dev/null || python3 -m pip install --quiet --break-system-packages aiohttp 2>/dev/null
echo "Starting... browser will open http://127.0.0.1:8800/"
( sleep 2 && open http://127.0.0.1:8800/ ) &
python3 server.py
