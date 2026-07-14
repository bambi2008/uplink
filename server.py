# -*- coding: utf-8 -*-
"""
Jake 稳定版 · 本地后端

为什么要后端：浏览器自带的语音识别在长通话里会掉线，这是它的固有缺陷。
本后端把"耳朵"换成 MiniMax 官方的实时流式识别（WebSocket 长连接），
由服务器稳定维护这条连接，浏览器只负责采集麦克风、播放声音。

职责：
  GET  /                     首页（static/index.html）
  WS   /ws/asr               浏览器把麦克风音频流推到这里 → 服务器转发给
                             MiniMax 实时识别 → 识别文字实时回传浏览器
  POST /api/chat             对话（流式，逐句返回，供前端边收边合成）
  POST /api/tts              语音合成（返回 mp3）
  POST /api/briefing         生成/读取当天 Jake 谈资
  POST /api/report           生成课后复盘

启动：
  pip install -r requirements.txt
  设置环境变量 MINIMAX_API_KEY（和可选 MINIMAX_GROUP_ID），或运行时在网页里填
  python server.py   →  打开 http://localhost:8800
"""

import os
import json
import asyncio
import time
import hashlib
import hmac
import base64
import pathlib
import contextlib
import uuid

import aiohttp
from aiohttp import web

# 豆包模块在这里就尝试加载一次，加载结果供 /api/diag 和 /api/asr_test 使用
_DB_OK, _DB_ERR = False, ""
try:
    import doubao
    _DB_OK = True
except Exception as _e:
    _DB_ERR = str(_e)

ROOT = pathlib.Path(__file__).parent
STATIC = ROOT / "static"

MINIMAX_BASE = "https://api.minimaxi.com"      # MiniMax 负责"想+说"
XF_RTASR_HOST = "rtasr.xfyun.cn"               # 讯飞负责"听"（实时语音转写）

CHAT_MODEL = "MiniMax-Text-01"                 # 非思考型：不打腹稿，接话快
CHAT_MODEL_FALLBACK = "MiniMax-M2.5-highspeed" # 若上者不可用自动回退
TTS_MODEL = "speech-2.8-turbo"

# MiniMax（对话+合成）
ENV_KEY = os.environ.get("MINIMAX_API_KEY", "")
ENV_GROUP = os.environ.get("MINIMAX_GROUP_ID", "")
# 讯飞（识别）：需要 APPID 和 APIKey
ENV_XF_APPID = os.environ.get("XF_APPID", "")
ENV_XF_APIKEY = os.environ.get("XF_APIKEY", "")


def key_from(req):
    return req.headers.get("X-MM-Key") or ENV_KEY


def group_from(req):
    return req.headers.get("X-MM-Group") or ENV_GROUP


def xf_handshake_url(appid, apikey):
    """按讯飞规范生成带鉴权的握手 URL：signa = base64(HmacSHA1(MD5(appid+ts), apikey))"""
    ts = str(int(time.time()))
    base = (appid + ts).encode("utf-8")
    md5 = hashlib.md5(base).hexdigest().encode("utf-8")
    signa = base64.b64encode(hmac.new(apikey.encode("utf-8"), md5, hashlib.sha1).digest()).decode("utf-8")
    from urllib.parse import quote
    # lang=cn 即"中英混合识别"（标准版合法值）；engLangType=1 自动中英文模式
    return (f"wss://{XF_RTASR_HOST}/v1/ws?appid={appid}&ts={ts}"
            f"&signa={quote(signa)}&lang=cn&engLangType=1")


# ----------------------------------------------------------------- 静态页

async def index(req):
    # no-store：文件一换、刷新即新，避免浏览器缓存旧页面导致"改了没生效"
    return web.FileResponse(STATIC / "index.html",
                            headers={"Cache-Control": "no-store, must-revalidate"})


# ----------------------------------------------------------------- 对话（流式）

async def api_chat(req):
    body = await req.json()
    messages = body.get("messages", [])
    key = key_from(req)
    if not key:
        return web.json_response({"error": "缺少 MiniMax API Key"}, status=400)

    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    await resp.prepare(req)

    payload = {"model": CHAT_MODEL, "temperature": 0.8, "stream": True, "messages": messages}
    # 流式状态机：过滤掉可能跨数据块的 <think>...</think> 思考段
    in_think = False
    carry = ""  # 暂存可能是"半个标签"的尾部，避免标签被切断漏判

    def filter_think(text):
        nonlocal in_think, carry
        s = carry + text
        carry = ""
        out = []
        while s:
            if in_think:
                i = s.find("</think>")
                if i == -1:
                    # 保留末尾一小段，防止 </think> 被切断
                    carry = s[-8:] if len(s) > 8 else s
                    return "".join(out)
                s = s[i + 8:]
                in_think = False
            else:
                i = s.find("<think>")
                if i == -1:
                    # 末尾若像半个 <think 标签，暂存等下一块
                    tail = s[-7:]
                    if "<" in tail:
                        cut = s.rfind("<")
                        out.append(s[:cut]); carry = s[cut:]; return "".join(out)
                    out.append(s); return "".join(out)
                out.append(s[:i]); s = s[i + 7:]; in_think = True
        return "".join(out)

    try:
        async with aiohttp.ClientSession() as s:
            wrote_any = False
            for model_try in (CHAT_MODEL, CHAT_MODEL_FALLBACK):
                payload["model"] = model_try
                async with s.post(MINIMAX_BASE + "/v1/chat/completions",
                                  headers={"Authorization": "Bearer " + key,
                                           "Content-Type": "application/json"},
                                  json=payload) as r:
                    if r.status >= 400:
                        continue  # 该模型不可用，换下一个
                    async for line in r.content:
                        line = line.decode("utf-8", "ignore").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        with contextlib.suppress(Exception):
                            delta = json.loads(data)["choices"][0]["delta"].get("content", "")
                            if delta:
                                clean = filter_think(delta)
                                if clean:
                                    wrote_any = True
                                    await resp.write(clean.encode("utf-8"))
                if wrote_any:
                    break
            if not wrote_any:
                await resp.write("\n[[ERROR]]对话模型均不可用（检查Key/余额）".encode("utf-8"))
    except Exception as e:
        with contextlib.suppress(Exception):
            await resp.write(("\n[[ERROR]]" + str(e)).encode("utf-8"))
    await resp.write_eof()
    return resp


# 非流式对话（生成谈资、复盘用）
async def mm_chat_once(key, messages, temperature=0.7):
    async with aiohttp.ClientSession() as s:
        for model_try in (CHAT_MODEL, CHAT_MODEL_FALLBACK):
            payload = {"model": model_try, "temperature": temperature, "messages": messages}
            async with s.post(MINIMAX_BASE + "/v1/chat/completions",
                              headers={"Authorization": "Bearer " + key,
                                       "Content-Type": "application/json"},
                              json=payload) as r:
                j = await r.json()
            try:
                txt = j["choices"][0]["message"]["content"] or ""
            except Exception:
                continue  # 该模型不可用，换下一个
            import re
            return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
    return ""


# ----------------------------------------------------------------- 语音合成

async def api_tts(req):
    body = await req.json()
    key = key_from(req)
    group = group_from(req)
    if not key:
        return web.json_response({"error": "缺少 MiniMax API Key"}, status=400)

    voice_setting = {"voice_id": body.get("voice", "English_magnetic_voiced_man"),
                     "speed": body.get("speed", 1.0)}
    if body.get("emotion"):
        voice_setting["emotion"] = body["emotion"]

    tts_model = body.get("model") or TTS_MODEL
    lang_boost = body.get("language_boost") or "auto"
    url = MINIMAX_BASE + "/v1/t2a_v2" + (("?GroupId=" + group) if group else "")
    payload = {"model": tts_model, "text": body.get("text", ""), "stream": False,
               "voice_setting": voice_setting, "language_boost": lang_boost,
               "audio_setting": {"format": "mp3"}}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers={"Authorization": "Bearer " + key,
                                        "Content-Type": "application/json"},
                          json=payload) as r:
            j = await r.json()
    hexaudio = (j.get("data") or {}).get("audio")
    if not hexaudio:
        return web.json_response({"error": str(j.get("base_resp") or j)[:200]}, status=502)
    audio = bytes.fromhex(hexaudio)
    return web.Response(body=audio, content_type="audio/mpeg")


# ----------------------------------------------------------------- 实时识别中转

async def ws_asr(req):
    """浏览器 <-> 本服务 <-> 讯飞实时转写。
    浏览器发来 16kHz/16bit 单声道 PCM；后端按讯飞要求每 40ms 送 1280 字节，
    解析讯飞返回的分片结果，拼成文字回传浏览器。连接稳定，长通话不掉。"""
    ws_client = web.WebSocketResponse(heartbeat=20)
    await ws_client.prepare(req)

    appid = req.query.get("appid") or ENV_XF_APPID
    apikey = req.query.get("apikey") or ENV_XF_APIKEY
    if not appid or not apikey:
        await ws_client.send_json({"type": "error", "msg": "缺少讯飞 APPID / APIKey"})
        await ws_client.close()
        return ws_client

    session = aiohttp.ClientSession()
    try:
        ws_xf = await session.ws_connect(xf_handshake_url(appid, apikey), heartbeat=20, timeout=30)
    except Exception as e:
        await ws_client.send_json({"type": "error", "msg": "无法连接讯飞识别：" + str(e)})
        await ws_client.close(); await session.close()
        return ws_client

    # 讯飞握手：第一条消息 action=started 表示成功
    try:
        first = await asyncio.wait_for(ws_xf.receive(), timeout=10)
        d = json.loads(first.data)
        if d.get("action") != "started":
            await ws_client.send_json({"type": "error", "msg": "讯飞握手失败：" + str(d)[:120]})
            await ws_xf.close(); await ws_client.close(); await session.close()
            return ws_client
    except Exception as e:
        await ws_client.send_json({"type": "error", "msg": "讯飞握手异常：" + str(e)})
        await ws_xf.close(); await ws_client.close(); await session.close()
        return ws_client

    await ws_client.send_json({"type": "ready"})

    # 浏览器 → 讯飞：攒够 1280 字节发一帧（约 40ms），尾音不足也及时补发，避免延迟
    buf = bytearray()

    async def pump_up():
        nonlocal buf
        async for msg in ws_client:
            if msg.type == aiohttp.WSMsgType.BINARY:
                buf += msg.data
                while len(buf) >= 1280:
                    chunk = bytes(buf[:1280]); del buf[:1280]
                    with contextlib.suppress(Exception):
                        await ws_xf.send_bytes(chunk)
                    await asyncio.sleep(0.04)
            elif msg.type == aiohttp.WSMsgType.TEXT and msg.data == "stop":
                # 结束标识：讯飞要求发 binary message，内容是 {"end": true}
                with contextlib.suppress(Exception):
                    if buf:
                        await ws_xf.send_bytes(bytes(buf)); buf = bytearray()
                    await ws_xf.send_bytes(b'{"end": true}')
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                break

    async def pump_down():
        async for msg in ws_xf:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            with contextlib.suppress(Exception):
                d = json.loads(msg.data)
                if d.get("action") == "error":
                    await ws_client.send_json({"type": "error", "msg": d.get("desc", "讯飞错误")})
                    break
                if d.get("action") == "result":
                    # data 字段本身是一个 JSON 字符串，需再解析一层
                    inner = d.get("data", "")
                    text, is_final = parse_xf_result(inner)
                    if text:
                        await ws_client.send_json({"type": "asr", "text": text, "final": is_final})

    up = asyncio.create_task(pump_up())
    down = asyncio.create_task(pump_down())
    await asyncio.wait([up, down], return_when=asyncio.FIRST_COMPLETED)
    for t in (up, down):
        t.cancel()
    for closer in (ws_xf.close(), session.close(), ws_client.close()):
        with contextlib.suppress(Exception):
            await closer
    return ws_client


def parse_xf_result(data_str):
    """解析讯飞识别结果 JSON，拼出这一片的文字，并判断是否为最终结果。
    结构：{cn:{st:{type:'0'|'1', rt:[{ws:[{cw:[{w:'字'}]}]}]}}}
    type '0' = 最终结果，'1' = 中间结果。"""
    try:
        d = json.loads(data_str)
        st = d["cn"]["st"]
        is_final = st.get("type") == "0"
        words = []
        for rt in st.get("rt", []):
            for w in rt.get("ws", []):
                for cw in w.get("cw", []):
                    words.append(cw.get("w", ""))
        return "".join(words), is_final
    except Exception:
        return "", False


# ----------------------------------------------------------------- 每日谈资

CARDS_SYSTEM = ("You are prepping Jake's memory for today's English practice call. "
    "Jake: 38, Seattle tech guy, wife, 7-year-old daughter, dog named Biscuit, lived in Beijing 5 years. "
    "His friend is an adult Chinese English learner.\n\n"
    "From the raw hot-topic titles below, PICK 3 that make great casual conversation between these two friends. "
    "EXCLUDE: extreme politics, elections, race, crime, violence, sexual content, tragedy, and anything needing "
    "deep US-local context. PREFER: work, money, tech, food, habits, everyday dilemmas.\n\n"
    "Write each as a first-person 'memory card' in Jake's voice (NOT a news summary): "
    "1) How I ran into it (casual, one vivid detail); 2) My take (mild opinion with a personal hook); "
    "3) What people were arguing about (like gossip); 4) A bridge question to life in China; "
    "5) Language gems: 3-4 colloquial expressions with 中文释义 in parentheses.\n\n"
    "Label them CARD 1 — OPENER, CARD 2 — BACKUP, CARD 3 — WILDCARD. Plain text only, in English.")


async def fetch_titles():
    titles = []
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout) as s:

        async def hn_item(i):
            with contextlib.suppress(Exception):
                async with s.get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json") as r:
                    it = await r.json()
                    if it and it.get("title"):
                        return f"[HN] {it['title']} ({it.get('score',0)} pts)"
            return None

        async def hn_all():
            with contextlib.suppress(Exception):
                async with s.get("https://hacker-news.firebaseio.com/v0/topstories.json") as r:
                    ids = (await r.json())[:20]
                # 20 条并发抓，而非逐条等待 —— 这是之前最慢的环节
                got = await asyncio.gather(*[hn_item(i) for i in ids])
                return [x for x in got if x]
            return []

        async def reddit_sub(sub):
            out = []
            with contextlib.suppress(Exception):
                async with s.get(f"https://www.reddit.com/r/{sub}/top.json?t=day&limit=8",
                                 headers={"User-Agent": "jake/0.2"}) as r:
                    j = await r.json()
                    for c in j["data"]["children"]:
                        d = c["data"]
                        if not d.get("over_18"):
                            out.append(f"[r/{sub}] {d['title']} ({d['score']} up)")
            return out

        # HN 和三个 subreddit 全部并发
        results = await asyncio.gather(
            hn_all(),
            reddit_sub("AskReddit"), reddit_sub("todayilearned"), reddit_sub("technology"),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, list):
                titles.extend(r)
    return titles


async def api_briefing(req):
    key = key_from(req)
    if not key:
        return web.json_response({"error": "缺少 API Key"}, status=400)
    import datetime
    today = datetime.date.today().isoformat()
    cache = ROOT / "briefings"
    cache.mkdir(exist_ok=True)
    f = cache / f"cards_{today}.txt"
    if f.exists():
        return web.json_response({"cards": f.read_text(encoding="utf-8")})

    # 抓热点：整步硬超时 8 秒，抓不到就让 MiniMax 现编
    titles = []
    try:
        titles = await asyncio.wait_for(fetch_titles(), timeout=8)
    except Exception:
        titles = []
    user = "\n".join(titles) if len(titles) >= 5 else (
        "No live topics. Invent 3 evergreen relatable topics (work culture, money habits, tech in daily life).")

    # 生成谈资：硬超时 20 秒，卡住就给一份内置的保底话题，保证前端一定能进通话
    try:
        cards = await asyncio.wait_for(
            mm_chat_once(key, [{"role": "system", "content": CARDS_SYSTEM},
                               {"role": "user", "content": user}], 0.9),
            timeout=20)
    except Exception:
        return web.json_response({"cards": FALLBACK_CARDS})

    if not cards or len(cards) < 40:
        cards = FALLBACK_CARDS
    f.write_text(cards, encoding="utf-8")
    return web.json_response({"cards": cards})


FALLBACK_CARDS = """CARD 1 — OPENER
How I ran into it: I was scrolling online this morning and saw a thread about people who negotiated their salary vs. those who just took the first offer.
My take: Honestly it hit home — when I joined my company I didn't push back at all, and my wife still teases me about it.
What people argued about: Half said always negotiate, the other half said pushing too hard can backfire.
Bridge: Is talking about salary kind of taboo in China too?
Language gems: hit home (说到心坎里), lowball offer (压低的报价), leave money on the table (白白错失利益), touchy subject (敏感话题)

CARD 2 — BACKUP
How I ran into it: Someone asked what small purchase under $50 genuinely improved your life.
My take: Mine's an electric kettle — five years in Beijing turned me into a hot-water person.
What people argued about: Whether spending on "sleep stuff" is smart or just giving up.
Bridge: What's the best cheap thing you ever bought?
Language gems: game changer (改变体验的东西), worth every penny (物超所值), a steal (超值), mundane (平淡无奇的)

CARD 3 — WILDCARD
How I ran into it: A company started making people justify any meeting over 30 minutes in writing.
My take: I'm torn — half my calendar could've been an email, but people will just game it.
What people argued about: "Finally" vs. "this just creates more paperwork."
Bridge: Are meetings as crazy in Chinese offices?
Language gems: this could've been an email (这会毫无必要), I'm torn (我很纠结), back-to-back (连轴转的), red tape (繁文缛节)"""


# ----------------------------------------------------------------- 复盘

REPORT_SYSTEM = ("你是一位资深英语教练。下面是学员和美国朋友Jake（母语者，雅思8.5+水平）的英语口语通话记录。"
    "用中文写一份简洁温暖、适合课后复习的复盘报告，Markdown格式："
    "## 今天聊了什么（2-3句）；"
    "## 永不豁免错误清单（时态/主谓一致/冠词/单复数这四类硬伤逐条列出：学员原话→正确说法→错误类型标签→一句话解释；没有就明确表扬'今天四类硬伤零犯错'；其他类型的错误也可列在后面，最多共8条）；"
    "## 说得更local（学员说得对但不够地道的表达→母语者的实际说法，3-5条，每条注明使用场景）；"
    "## 发音·连读·语调教练（从学员今天说过的句子里挑3-5个短语，标注母语者怎么读：连读如 picked it up→pick-di-dup、弱读如 want to→wanna、重音落在哪个词、句尾语调升降。开头注明：这是基于文字的目标读法教学，Jake听不到实际发音，真实发音诊断需录音评测）；"
    "## 值得记住的高级表达（Jake今天用过的3-5个，附中文释义和新例句）；"
    "## 课后自测（3道小题：给出中文意思，请学员回忆并说出今天学到的英文表达；参考答案附在最后）；"
    "## 下次挑战（1个具体小目标，优先针对今天的永不豁免错误）；"
    "## 下一课预告（这是下节课的教案：1)主题——延续学员今天表现出的兴趣点、或针对暴露的薄弱点，定一个具体的话题；"
    "2)预习表达——6个与主题相关、值得课前熟悉的地道表达，英文+中文释义；"
    "3)热身问题——2个英文问题，学员可以提前想想怎么答）。"
    "语气像朋友。表现好要具体指出好在哪。")


async def api_report(req):
    body = await req.json()
    key = key_from(req)
    convo = body.get("transcript", "")[-20000:]
    if not key or not convo:
        return web.json_response({"report": "（本次没有可用记录）"})
    rep = await mm_chat_once(key, [{"role": "system", "content": REPORT_SYSTEM},
                                   {"role": "user", "content": convo}], 0.6)
    return web.json_response({"report": rep})


# ----------------------------------------------------------------- 路由

BUILD = "2026-07-14.gem1"

# ----------------------------------------------------------------- 发音评测（讯飞 ISE 流式版）

XF_ISE_HOST = "ise-api.xfyun.cn"
XF_ISE_PATH = "/v2/open-ise"


def ise_url(api_key, api_secret):
    """讯飞 v2 WebAPI 标准签名：hmac-sha256(host+date+request-line)。"""
    from email.utils import formatdate
    from urllib.parse import quote
    date = formatdate(usegmt=True)
    origin = f"host: {XF_ISE_HOST}\ndate: {date}\nGET {XF_ISE_PATH} HTTP/1.1"
    sig = base64.b64encode(hmac.new(api_secret.encode(), origin.encode(), hashlib.sha256).digest()).decode()
    auth_origin = (f'api_key="{api_key}", algorithm="hmac-sha256", '
                   f'headers="host date request-line", signature="{sig}"')
    auth = base64.b64encode(auth_origin.encode()).decode()
    return (f"wss://{XF_ISE_HOST}{XF_ISE_PATH}?authorization={quote(auth)}"
            f"&date={quote(date)}&host={XF_ISE_HOST}")


def parse_ise_xml(xml_bytes):
    """解析评测结果XML → 统一成 /100 分制 + 逐词问题清单。防御式解析，字段缺了不炸。"""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_bytes)

    def f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # 找带 total_score 的评分节点（rec_paper 下的 read_sentence/read_chapter）
    node = None
    for el in root.iter():
        if el.get("total_score") is not None:
            node = el
            if el.get("accuracy_score") is not None:
                break
    if node is None:
        return {"ok": False, "detail": "结果里没有评分节点"}
    total, acc, flu = f(node.get("total_score")), f(node.get("accuracy_score")), f(node.get("fluency_score"))
    rejected = str(node.get("is_rejected", "false")).lower() == "true"

    # 英文默认5分制 → 统一换算成百分制
    scale = 20.0 if (total is not None and total <= 5.01) else 1.0

    def pct(v):
        return round(v * scale) if v is not None else None

    words = []
    for w in root.iter("word"):
        content = (w.get("content") or "").strip()
        if not content or content in ("silv", "sil", "fil"):
            continue
        wscore = f(w.get("total_score"))
        bad_phones = []
        for p in w.iter("phone"):
            flags = [p.get("perr_msg"), p.get("dp_message"), p.get("serr_msg")]
            if any(x not in (None, "0") for x in flags):
                pc = (p.get("content") or "").strip()
                if pc and pc not in bad_phones:
                    bad_phones.append(pc)
        dp = w.get("dp_message", "0")
        words.append({"w": content,
                      "score": pct(wscore),
                      "bad": bad_phones,
                      "dp": dp})
    return {"ok": True, "total": pct(total), "accuracy": pct(acc), "fluency": pct(flu),
            "rejected": rejected, "words": words}


async def api_ise(req):
    """收前端一段跟读PCM+目标句 → 讯飞语音评测 → 返回统一评分JSON。"""
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"ok": False, "detail": "请求体不是JSON"})
    appid = body.get("appid", ""); key = body.get("apikey", ""); secret = body.get("apisecret", "")
    text = (body.get("text") or "").strip()
    pcm_b64 = body.get("pcm") or ""
    if not (appid and key and secret and text and pcm_b64):
        return web.json_response({"ok": False, "detail": "缺参数（appid/apikey/apisecret/text/pcm）"})
    try:
        pcm = base64.b64decode(pcm_b64)
    except Exception:
        return web.json_response({"ok": False, "detail": "pcm不是有效base64"})
    pcm = pcm[:16000 * 2 * 170]   # ISE上限3分钟，留余量

    ssb = {"common": {"app_id": appid},
           "business": {"sub": "ise", "ent": "en_vip", "category": "read_sentence",
                        "cmd": "ssb", "auf": "audio/L16;rate=16000", "aue": "raw",
                        "text": "\ufeff" + text, "tte": "utf-8", "ttp_skip": True,
                        "rstcd": "utf8", "group": "adult"},
           "data": {"status": 0, "data": ""}}
    session = aiohttp.ClientSession()
    try:
        ws = await session.ws_connect(ise_url(key, secret), timeout=15)
        await ws.send_str(json.dumps(ssb))
        # 音频分片：首帧aus=1，中间aus=2，收尾发一个空的aus=4/status=2
        CH = 4096
        chunks = [pcm[i:i + CH] for i in range(0, len(pcm), CH)] or [b""]
        for i, c in enumerate(chunks):
            frame = {"business": {"cmd": "auw", "aus": 1 if i == 0 else 2, "aue": "raw"},
                     "data": {"status": 1, "data": base64.b64encode(c).decode(),
                              "data_type": 1, "encoding": "raw"}}
            await ws.send_str(json.dumps(frame))
            await asyncio.sleep(0.01)
        await ws.send_str(json.dumps({"business": {"cmd": "auw", "aus": 4, "aue": "raw"},
                                      "data": {"status": 2, "data": "", "data_type": 1, "encoding": "raw"}}))
        # 等最终结果
        xml_bytes = None
        while True:
            msg = await asyncio.wait_for(ws.receive(), timeout=20)
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
                continue
            j = json.loads(msg.data)
            if j.get("code") != 0:
                await ws.close()
                return web.json_response({"ok": False, "detail": f"讯飞评测拒绝 code={j.get('code')} {j.get('message', '')}"})
            d = j.get("data") or {}
            if d.get("status") == 2:
                xml_bytes = base64.b64decode(d.get("data", ""))
                break
        await ws.close()
        if not xml_bytes:
            return web.json_response({"ok": False, "detail": "评测服务没有返回结果"})
        return web.json_response(parse_ise_xml(xml_bytes))
    except aiohttp.WSServerHandshakeError as e:
        return web.json_response({"ok": False, "detail": f"评测握手被拒 HTTP {e.status}（多半是评测的APIKey/APISecret不对，或「语音评测(流式版)」未开通）"})
    except asyncio.TimeoutError:
        return web.json_response({"ok": False, "detail": "评测超时"})
    except Exception as e:
        return web.json_response({"ok": False, "detail": f"{type(e).__name__} {str(e)[:120]}"})
    finally:
        await session.close()



async def api_diag(req):
    """让前端确认：server 是新版、doubao 模块是否加载成功。"""
    return web.json_response({"build": BUILD, "doubao_loaded": _DB_OK, "doubao_error": _DB_ERR})


async def api_asr_test(req):
    """服务器亲自去连一次识别后端，把最原始的握手结果端给前端看。"""
    engine = req.query.get("engine", "xf")
    out = {"ok": False, "engine": engine, "detail": ""}
    session = aiohttp.ClientSession()
    try:
        if engine == "db":
            if not _DB_OK:
                out["detail"] = "server 未加载豆包模块：" + _DB_ERR
                return web.json_response(out)
            appid = req.query.get("appid", ""); token = req.query.get("token", "")
            if not appid or not token:
                out["detail"] = "App ID 或 Access Token 为空"
                return web.json_response(out)
            base_headers = {"X-Api-App-Key": appid, "X-Api-Access-Key": token}
            lines = []
            ok_any = False
            for res_id in ("volc.bigasr.sauc.duration", "volc.bigasr.sauc.concurrent"):
                tag = "时长版" if res_id.endswith("duration") else "并发版"
                headers = dict(base_headers, **{"X-Api-Resource-Id": res_id,
                                                "X-Api-Connect-Id": str(uuid.uuid4())})
                try:
                    ws = await session.ws_connect("wss://" + doubao.DB_HOST + doubao.DB_PATH,
                                                  headers=headers, timeout=15)
                    params = {"user": {"uid": "test"},
                              "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1},
                              "request": {"model_name": "bigmodel"}}
                    await ws.send_bytes(doubao._full_client_request(1, params))
                    first = await asyncio.wait_for(ws.receive(), timeout=8)
                    if first.type == aiohttp.WSMsgType.BINARY:
                        p = doubao._parse(first.data)
                        if p and p["type"] == doubao.SERVER_ERROR_RESPONSE:
                            m = (p["body"] or {}).get("message") if isinstance(p["body"], dict) else ""
                            lines.append(f"{tag}：拒绝 code={p['code']} {m or ''}")
                        else:
                            ok_any = True
                            lines.append(f"{tag}：握手成功 ✓（豆包将自动使用这个版本）")
                    else:
                        lines.append(f"{tag}：返回意外帧 type={first.type}")
                    await ws.close()
                except aiohttp.WSServerHandshakeError as e:
                    hint = (e.headers.get("X-Api-Message", "") if e.headers else "") or e.message
                    logid = e.headers.get("X-Tt-Logid", "") if e.headers else ""
                    lines.append(f"{tag}：HTTP {e.status} {hint}" + (f"（logid {logid[:24]}）" if logid else ""))
                except Exception as e:
                    lines.append(f"{tag}：{type(e).__name__} {str(e)[:100]}")
            out["ok"] = ok_any
            out["detail"] = "\n".join(lines)
            if not ok_any:
                out["detail"] += "\n两个版本都被拒 → 请到火山控制台确认：1)「流式语音识别大模型」已开通/已领免费额度 2)Access Token 完整无空格 3)App ID 与 Token 属同一应用"
        else:
            appid = req.query.get("appid", ""); apikey = req.query.get("apikey", "")
            if not appid or not apikey:
                out["detail"] = "APPID 或 APIKey 为空"
                return web.json_response(out)
            try:
                ws = await session.ws_connect(xf_handshake_url(appid, apikey), timeout=15)
                first = await asyncio.wait_for(ws.receive(), timeout=8)
                ok = False; msg = str(first.data)[:200]
                if first.type == aiohttp.WSMsgType.TEXT:
                    j = json.loads(first.data)
                    ok = j.get("action") == "started"
                    if not ok: msg = j.get("desc") or j.get("code") or msg
                out["ok"] = ok
                out["detail"] = "握手成功，讯飞可用 ✓" if ok else f"讯飞拒绝：{msg}"
                await ws.close()
            except Exception as e:
                out["detail"] = f"连不上讯飞：{type(e).__name__} {str(e)[:140]}"
        return web.json_response(out)
    finally:
        await session.close()


def make_app():
    app = web.Application(client_max_size=1024 * 1024 * 8)
    app.router.add_get("/", index)
    app.router.add_get("/ws/asr", ws_asr)
    try:
        import doubao
        app.router.add_get("/ws/doubao", doubao.doubao_relay)
    except Exception as _e:
        print("豆包模块未加载：", _e)
    app.router.add_get("/api/diag", api_diag)
    app.router.add_get("/api/asr_test", api_asr_test)
    app.router.add_post("/api/ise", api_ise)
    app.router.add_post("/api/chat", api_chat)
    app.router.add_post("/api/tts", api_tts)
    app.router.add_post("/api/briefing", api_briefing)
    app.router.add_post("/api/report", api_report)
    app.router.add_static("/static/", STATIC)
    return app


if __name__ == "__main__":
    print(f"\n  Uplink 已就位（build {BUILD}）→  打开浏览器访问  http://localhost:8800")
    print(f"  豆包识别模块：{'已加载 ✓' if _DB_OK else '未加载 ✗ ' + _DB_ERR}\n")
    web.run_app(make_app(), host="127.0.0.1", port=8800, print=None)
