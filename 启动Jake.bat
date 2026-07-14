@echo off
title Uplink
cd /d "%~dp0"
echo [Uplink] 正在准备(首次启动会自动安装一个小组件, 需联网)...
python -m pip install --quiet aiohttp >nul 2>&1
if errorlevel 1 py -m pip install --quiet aiohttp >nul 2>&1
echo [Uplink] 启动中... 浏览器稍后自动打开; 若没打开, 手动访问 http://localhost:8800
start "" http://localhost:8800
python server.py 2>nul || py server.py
pause
