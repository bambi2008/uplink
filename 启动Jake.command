#!/bin/bash
# macOS 一键启动：双击本文件即可
cd "$(dirname "$0")"
echo "正在准备 Uplink（首次启动会自动安装一个小组件，需联网）…"
python3 -m pip install --quiet --user aiohttp 2>/dev/null || python3 -m pip install --quiet --break-system-packages aiohttp 2>/dev/null
echo "启动中…浏览器稍后会自动打开。若没打开，手动访问 http://localhost:8800"
( sleep 2 && open http://localhost:8800 ) &
python3 server.py
