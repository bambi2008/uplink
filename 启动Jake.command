#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "Creating an isolated Python environment..."
  python3 -m venv .venv
fi
if ! .venv/bin/python -c "import aiohttp" >/dev/null 2>&1; then
  echo "Installing dependencies for the first run..."
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python launcher.py
