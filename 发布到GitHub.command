#!/bin/bash
cd "$(dirname "$0")"
echo "== Uplink 发布到 GitHub (bambi2008/uplink) =="
echo "第一次发布前，先在浏览器建好空仓库：https://github.com/new  仓库名: uplink（什么都不要勾）"
read -p "按回车开始推送（会要求 GitHub 认证一次）..."
git remote remove origin 2>/dev/null
git remote add origin https://github.com/bambi2008/uplink.git
git branch -M main
git push -u origin main && echo "✅ 发布成功：https://github.com/bambi2008/uplink"
