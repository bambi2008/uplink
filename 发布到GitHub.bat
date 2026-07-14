@echo off
title Uplink - 发布到 GitHub
cd /d "%~dp0"
echo.
echo  == Uplink 发布到 GitHub (bambi2008/uplink) ==
echo.
echo  第一次发布前, 请先在浏览器里建好空仓库:
echo    https://github.com/new  仓库名填: uplink  (什么都不要勾选)
echo.
echo  按任意键开始推送(会弹出 GitHub 登录窗, 认证一次即可)...
pause >nul
git remote remove origin >nul 2>&1
git remote add origin https://github.com/bambi2008/uplink.git
git branch -M main
git push -u origin main
echo.
if errorlevel 1 (
  echo  推送失败? 常见原因: 1.仓库还没在网页上创建  2.认证窗被关掉了  3.没装Git
  echo  装Git: https://git-scm.com/download/win  装好重开本脚本
) else (
  echo  发布成功! 仓库地址: https://github.com/bambi2008/uplink
)
pause
