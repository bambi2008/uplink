# Uplink 🛰️

> **接通了，就有人陪你说英语。**
>
> 每天 30 分钟，接通"近地轨道"那头的 Jake，用英语好好说说话。
> 他是雅思 8.5+ 水平的母语者，也说流利中文——你卡壳时，他用中文接住你。
> （Uplink 是这个产品；Jake 是里面那个陪你说话的人。）

![界面世界观：透过航天器舷窗看地球，Jake 是地面控制](docs/screenshot.png)

## 它是什么

一个自托管的英语口语陪练网站：

- **像朋友，不像老师**：Jake 每天"刷"论坛热点（Reddit / Hacker News）当谈资，主动带话题，有观点、有情绪、会笑会叹气
- **即时纠错 + 表达升级**：说错当场纠，说得平淡给你更地道的说法
- **中文兜底**：卡壳直接说中文，他用中文接住、教你地道英文说法、再把你拉回英语
- **课后复盘**：每次通话自动生成中文复盘报告（纠错回顾、高级表达、课后自测），存入"飞行日志"可反复复习
- **沉浸式界面**：航天舷窗世界观——自转的地球、真实时间晨昏线、失重漂浮物、通讯电文字幕

## 技术栈

- **对话 + 语音合成**：MiniMax（M2.5 对话 + speech-2.8 情绪化语音）
- **语音识别**：讯飞实时转写（后端 WebSocket 长连接）+ 浏览器识别自动兜底
- **后端**：Python + aiohttp（本地单文件服务，也负责抓取每日热点、生成谈资与复盘）
- **前端**：单文件 HTML/CSS/JS，零框架

## 快速开始

需要：Python 3.9+、MiniMax API Key（[platform.minimaxi.com](https://platform.minimaxi.com)）、可选讯飞凭据（[xfyun.cn](https://www.xfyun.cn) 开通「实时语音转写」）。

```bash
pip install -r requirements.txt
python server.py
# 打开 http://127.0.0.1:8800/，点「通讯设置」填入密钥，然后「建立通讯」
```

Windows 可直接双击 `启动Jake.bat`，macOS 双击 `启动Jake.command`（自动装依赖并打开浏览器）。

## iPhone / 安卓手机

手机端沿用同一套 Uplink 功能和电脑端私有配置。MiniMax、讯飞、豆包的 Key、Token、Secret 不会保存到手机或写进安装包。

### 长期使用：固定私密地址（推荐）

1. 在电脑和手机安装 [Tailscale](https://tailscale.com/download)，并登录同一个账户。
2. 双击 `启动手机端_固定地址.bat`。
3. 用 iPhone 相机或安卓扫码打开电脑显示的二维码，首次使用允许麦克风。
4. iPhone：Safari 分享菜单 → `添加到主屏幕`。安卓：Chrome 菜单 → `安装应用`。

此方式只在你的 Tailscale 私有网络内开放，手机地址固定。电脑重启后重新双击启动脚本，手机主屏幕图标仍可使用，无需重新填写第三方 API 凭据。

### 临时测试：免装手机网络工具

双击 `启动手机端.bat`，确认提示后，脚本会通过 Cloudflare Quick Tunnel 建立临时 HTTPS 地址并显示二维码。关闭窗口后入口失效；下次启动会生成新地址，因此需要重新扫码。此模式的通话流量会经 Cloudflare 加密转发，适合测试，不建议作为长期固定入口。

手机通话时保持启动窗口运行。iPhone/安卓切到后台后，系统可能暂停麦克风；回到 Uplink 时应用会自动恢复音频和识别连接。若 iPhone 主屏幕模式偶发无法恢复麦克风，可直接在 Safari 打开同一地址继续使用。

## 面向客户的商业运行模式

商业模式与个人桌面模式相互隔离。客户通过公开 HTTPS 地址注册登录，不需要打开电脑，
也不需要填写 MiniMax、讯飞或豆包凭据。供应商凭据只由云端 Secret 管理器注入；客户
偏好、每日额度和飞行复盘按账号隔离。

```bash
docker build -t uplink .
docker run --env-file .env.commercial -p 8800:8800 -v uplink-data:/data uplink
```

生产部署说明和上线前必做项见 [`docs/commercial-deployment.md`](docs/commercial-deployment.md)。
`.env.commercial.example` 只包含变量名，不包含任何真实凭据。原有 `启动Jake.bat` 仍然
启动个人桌面模式，行为不变。

## 低延迟开关

P0 低延迟机制默认开启，开发或故障回滚时可在启动服务前独立关闭：

```text
UPLINK_LATENCY_TRACE=0  # 默认关闭；设为 1 才写匿名延迟 JSONL
UPLINK_LOW_LATENCY=0    # 恢复旧 Chat/TTS 请求实现
UPLINK_FAST_EOT=0       # 恢复旧停顿与 ASR 收尾时序
UPLINK_STREAM_TTS=0     # 完整恢复 /api/tts blob 播放路径
```

这些开关不改变也不清空本机通讯设置。详细测量口径、回滚顺序与已知限制见
[`docs/latency-p0-report.md`](docs/latency-p0-report.md)。

## 项目结构

```
server.py            # 后端：识别中转 / 对话流式转发 / 语音合成 / 每日谈资 / 复盘
static/index.html    # 前端：舷窗界面 + 通话状态机 + 复习本（单文件）
scripts/             # 匿名延迟汇总与真实上游基准脚本
启动Jake.bat/.command # 一键启动
```

## 隐私

所有 API Key 只保存在你本机浏览器 localStorage，经由你自己电脑上的后端与各服务通信，不经过任何第三方服务器。对话记录仅存本机。

## Roadmap

- [ ] 长期记忆：Jake 记住你聊过什么、常犯什么错
- [ ] 发音专项：接入音素级发音评估
- [ ] 视频形象：接入实时数字人（Tavus / HeyGen）

---

*Built with Claude. 一个中年人的英语学习实验。*
