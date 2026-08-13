# Uplink P0 低延迟报告

## 测试环境

| 项目 | 值 |
|---|---|
| 测试日期 | 2026-08-13（Asia/Shanghai） |
| 基线 Git SHA | `b1bfe1b654e063e8ca6871407052d499db95ecde` |
| PR 1 Git SHA | `9c2a24f` |
| PR 2 Git SHA | `2cd5605` |
| PR 3 Git SHA | 本报告所在提交；最终完整 SHA 见交付回复 |
| 操作系统 | Windows NT 10.0.26200.0 |
| 浏览器 | Playwright Chromium 实测；宿主 Chrome 151.0.7922.77 |
| ASR 引擎 | 本轮自动 benchmark 未接入麦克风或 ASR；讯飞、豆包及浏览器 fallback 只做既有回归 |
| Chat 模型 | `MiniMax-Text-01`（真实响应头与匿名日志确认） |
| TTS 模型 | `speech-2.8-turbo`，MP3 32kHz/128kbps/mono |
| 网络环境 | 同一台本地电脑、同一网络访问 MiniMax 中国站；ISP、带宽及抖动未单独测量 |
| 样本数 | 每阶段 1 次 warm-up 后 30 个有效样本；短句/中句/思考停顿句各 10 个 |

## 口径

最终 30 轮数据由 `scripts/benchmark_latency.py` 通过本地 `/api/chat` 与 `/ws/tts`
调用真实 MiniMax 服务。脚本同时预连 TTS 和请求 Chat，收到第一个自然可朗读片段后才发送
`task_start`/`task_continue`，只保存匿名编号、句型分类和阶段耗时，不保存回复文本、Key 或音频。

这不是完整的麦克风端到端测试：它不包含真实麦克风活动、ASR 尾帧、浏览器设备输出及人工听感。
因此 `pipeline_first_audio_ms` 表示“本地发起 Chat 请求到收到首个 MP3 chunk”，不能冒充
`Semantic First Audio Latency`。浏览器另做了一次真实 MediaSource 冒烟测试，确认首块 append 后触发
`Audio.playing`，但单次冒烟不纳入 P50/P90。

## 分阶段结果

单位均为毫秒。`n/a` 表示当时没有按该口径保留足够数据，不做反推或补造。

| 指标 | Baseline P50/P90 | PR 1 P50/P90 | PR 2 P50/P90 | PR 3 P50/P90/P95 |
|---|---:|---:|---:|---:|
| End-of-turn | 约 2100（旧固定路径，不是实测分位数） | 同基线 | 强完成约 730；中性约 930；未完成可到约 1840（状态机固定值） | 同 PR 2 |
| LLM first raw byte | 667.5 / 808.8 | 373.2 / 494.5 | n/a（仅真实 smoke） | 626.4 / 884.8 / 903.3 |
| LLM first speakable text | 1387.4 / 1613.8 | 1053.3 / 1253.7 | n/a（仅真实 smoke） | 1316.8 / 1633.0 / 1665.8 |
| TTS first audio | 1176.2 / 1404.8（完整 blob） | 725.6 / 939.3（完整 blob） | n/a（仅真实 smoke） | 291.8 / 324.7 / 347.4（首个流 chunk） |
| Chat→首音代理值 | 2563.6 / 3018.6（两段分位数串行和） | 1778.9 / 2193.0（两段分位数串行和） | n/a | 1597.1 / 1918.8 / 1958.2（逐轮真实并行管线） |
| Interrupt stop | n/a | n/a | 自动化状态测试通过，无真实毫秒样本 | 自动化状态测试通过，无真实毫秒样本 |

PR 3 的完整真实上游统计：

| 指标 | count | P50 | P90 | P95 | min | max |
|---|---:|---:|---:|---:|---:|---:|
| Chat complete | 30 | 1481.1 | 1894.7 | 2006.3 | 960.1 | 2205.5 |
| Chat first raw | 30 | 626.4 | 884.8 | 903.3 | 462.8 | 976.2 |
| Chat first speakable | 30 | 1316.8 | 1633.0 | 1665.8 | 939.8 | 1686.4 |
| TTS ready（与 Chat 并行预连） | 30 | 617.8 | 876.9 | 895.1 | 454.1 | 967.6 |
| TTS start→first audio | 30 | 291.8 | 324.7 | 347.4 | 246.9 | 510.7 |
| Chat request→first audio | 30 | 1597.1 | 1918.8 | 1958.2 | 1210.5 | 2192.0 |
| TTS stream complete | 30 | 743.6 | 1018.6 | 1064.5 | 577.4 | 1514.8 |

相对基线的 Chat→首音代理值改善：P50 为 37.7%，P90 为 36.4%。这里的基线是
“首个可朗读文本分位数 + 完整 blob TTS 分位数”，PR 3 是逐轮并行实测，统计方法并不完全相同，
所以该比例用于方向性验收，不替代完整语音端到端 A/B。

## 质量与失败计数

| 项目 | 结果 |
|---|---:|
| 提前截断次数 | 未做真实麦克风 30 轮，不能给出有效计数 |
| ASR empty | 未做真实麦克风 30 轮，不能给出有效计数 |
| Chat fallback | 0 / 30 |
| TTS fallback | 0 / 30 |
| 失败次数 | 0 / 30 |
| stale audio | 自动化测试 0；未做人工 30 轮计数 |
| duplicate playback | 自动化测试 0；未做人工 30 轮计数 |
| 浏览器控制台 | 0 error，0 warning |
| 两段真实流协议 | 29 chunks、50202 bytes，事件顺序正确 |

## 结论

最低合并标准“相对基线 P50 至少改善 30%”在后端 Chat→首音代理口径达到，P90 也达到 30% 改善。
但目标 `P50 <= 1.2s / P90 <= 1.8s` **尚未达到**：即使排除 turn ending、ASR 和浏览器播放，
真实后端管线已是 P50 1.597s / P90 1.919s。剩余延迟主要位于 Chat 生成首个自然可朗读片段
（P50 1.317s / P90 1.633s），TTS 流首块本身仅 P50 292ms / P90 325ms。

## 功能开关与回滚

```text
UPLINK_LATENCY_TRACE=0  # 关闭匿名日志；关闭后 /api/latency 返回 204 且不写文件
UPLINK_LOW_LATENCY=0    # 恢复旧后端 Chat/TTS 实现
UPLINK_FAST_EOT=0       # 恢复旧 950ms grace + 旧 ASR flush
UPLINK_STREAM_TTS=0     # 不创建 StreamingReplyPlayer，完整走 /api/tts blob
```

建议按风险最小顺序回滚：先只关 `UPLINK_STREAM_TTS`；若仍有停顿截断，再关 `UPLINK_FAST_EOT`；
最后才关 `UPLINK_LOW_LATENCY`。这些开关不读写通讯设置，不需要重新填写 MiniMax、讯飞或豆包凭据。

## 已验证

- 共享 `aiohttp.ClientSession` 在 app cleanup 时关闭。
- 模型负缓存只接受明确的 400/404 model unavailable，401/403/429/5xx/网络错误不缓存。
- acknowledgment 与 semantic first audio 分开计时。
- 中文、英文首段均可用 Turbo；filler warmup 不在通话启动关键路径。
- pending EOT 可被继续说话取消；同一 turn 只进入一次 `finishTurn()`。
- MediaSource chunk 排队、`updateend`、`endOfStream()`、旧 generation 丢弃和取消。
- 流失败前未播放时转 blob，开始播放后不从头双播。
- 真实 MiniMax WebSocket 单段、两段、cancel-safe relay 与浏览器 `Audio.playing`。
- 原 `/api/tts` 音色试听/fallback 接口真实返回 `audio/mpeg`。
- 本机设置文件 SHA-256 在开发、基准与回归前后保持不变。

## 尚未验证与风险

- 未完成 30 轮真人麦克风→讯飞/豆包 ASR→真实扬声器播放的端到端 A/B，故不能提供真实
  `semantic_first_audio_total_ms`、提前截断率或 interruption 毫秒分位数。
- 未在 Safari/Firefox 上验证 MP3 MediaSource；不支持时会自动走 blob。
- 网络高抖动时 Chat P90/P95 仍明显升高；当前瓶颈不是 TTS。
- MiniMax WebSocket 的长期空闲连接寿命受上游策略影响；本实现每个 reply 一条连接，并在 Chat 生成期间预连，
  不在连接上复用第二个 reply。
- 自动化没有调用真实讯飞、豆包和发音评测服务，以免依赖凭据或产生不可控外部状态；既有路径仅由状态测试守护。

复现实测：

```powershell
$env:UPLINK_LATENCY_TRACE='1'
python server.py
python scripts/benchmark_latency.py --output latency-logs/pr3-benchmark-v2.jsonl
python scripts/summarize_latency.py latency-logs/pr3-benchmark-v2.jsonl
```
