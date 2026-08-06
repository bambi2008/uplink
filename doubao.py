# -*- coding: utf-8 -*-
"""
豆包（火山引擎）大模型流式语音识别 · 本地中转

和 server.py 里的讯飞中转（/ws/asr）并列，作为第二个「耳朵」。
对浏览器这一侧说的协议和讯飞完全一样，前端不用区分：
  - 浏览器发来：16kHz / 16bit / 单声道 / 小端 PCM 二进制帧；结束时发文本 "stop"
  - 中转回浏览器：{"type":"ready"} / {"type":"error","msg":...} / {"type":"asr","text":...,"final":true|false}

对火山引擎这一侧走大模型流式识别 v3 协议：
  wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
  鉴权用请求头 X-Api-App-Key / X-Api-Access-Key / X-Api-Resource-Id
  数据是二进制帧：4 字节 header + （可选 4 字节序列号）+ 4 字节 payload 长度 + payload
  header 四个字节依次为：
    (协议版本<<4 | header长度)  (消息类型<<4 | 标志位)  (序列化<<4 | 压缩)  保留
  整数一律大端；payload 为 gzip 压缩后的 JSON（配置）或原始 PCM（音频）。

凭据获取：console.volcengine.com/speech → 创建应用 → 勾选「语音识别大模型/流式」，
拿到 App ID 和 Access Token；资源 ID 默认用时长版 volc.bigasr.sauc.duration（有免费额度）。
"""

import os
import json
import gzip
import uuid
import struct
import asyncio
import contextlib

import aiohttp
from aiohttp import web

DB_HOST = "openspeech.bytedance.com"
DB_PATH = "/api/v3/sauc/bigmodel"

# 环境变量兜底（也可在网页设置里填，随查询串传进来）
ENV_DB_APPID = os.environ.get("DB_APPID", "")
ENV_DB_TOKEN = os.environ.get("DB_TOKEN", "")
ENV_DB_RESOURCE = os.environ.get("DB_RESOURCE", "volc.bigasr.sauc.duration")

# ---- 协议常量 ----
PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001

FULL_CLIENT_REQUEST = 0b0001
AUDIO_ONLY_REQUEST = 0b0010
FULL_SERVER_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

NO_SEQUENCE = 0b0000
POS_SEQUENCE = 0b0001
NEG_SEQUENCE = 0b0010
NEG_WITH_SEQUENCE = 0b0011

NO_SERIALIZATION = 0b0000
JSON = 0b0001
NO_COMPRESSION = 0b0000
GZIP = 0b0001


def _header(message_type, flags, serial=JSON, comp=GZIP):
    """4 字节可变 header。"""
    return bytes([
        (PROTOCOL_VERSION << 4) | DEFAULT_HEADER_SIZE,
        (message_type << 4) | flags,
        (serial << 4) | comp,
        0x00,
    ])


def _seq(sequence):
    """序列号：有符号大端 int32（最后一包用负数）。"""
    return struct.pack(">i", sequence)


def _full_client_request(sequence, params):
    """首包：配置。gzip(JSON)。"""
    payload = gzip.compress(json.dumps(params).encode("utf-8"))
    return _header(FULL_CLIENT_REQUEST, POS_SEQUENCE) + _seq(sequence) + struct.pack(">I", len(payload)) + payload


def _audio_request(sequence, pcm, last=False):
    """音频包：gzip(raw PCM)。header 沿用默认 JSON 序列化位（与官方 demo 一致，服务端对音频包忽略该位）。"""
    payload = gzip.compress(pcm)
    flags = NEG_WITH_SEQUENCE if last else POS_SEQUENCE
    seqv = -sequence if last else sequence
    return _header(AUDIO_ONLY_REQUEST, flags) + _seq(seqv) + struct.pack(">I", len(payload)) + payload


def _parse(res):
    """解析服务端二进制帧，返回 dict：{type, is_last, code, body}。body 为解析后的 JSON（dict）或 None。"""
    if not res or len(res) < 4:
        return None
    header_size = res[0] & 0x0F
    msg_type = res[1] >> 4
    flags = res[1] & 0x0F
    serial = res[2] >> 4
    comp = res[2] & 0x0F
    payload = res[header_size * 4:]

    out = {"type": msg_type, "is_last": bool(flags & 0x02), "code": None, "body": None}

    # 带序列号则先剥掉 4 字节
    if flags & 0x01:
        payload = payload[4:]

    if msg_type == SERVER_ERROR_RESPONSE:
        if len(payload) >= 8:
            out["code"] = struct.unpack(">I", payload[:4])[0]
            size = struct.unpack(">I", payload[4:8])[0]
            payload = payload[8:8 + size] if len(payload) >= 8 + size else payload[8:]
    else:
        # FULL_SERVER_RESPONSE / SERVER_ACK：4 字节 payload 长度 + payload
        if len(payload) >= 4:
            size = struct.unpack(">I", payload[:4])[0]
            payload = payload[4:4 + size] if len(payload) >= 4 + size else payload[4:]

    if comp == GZIP and payload:
        with contextlib.suppress(Exception):
            payload = gzip.decompress(payload)
    if serial == JSON and payload:
        with contextlib.suppress(Exception):
            out["body"] = json.loads(payload)
    return out


def _extract(body):
    """从服务端 body 里取 (当前文本, 是否为该分句最终结果)。"""
    if not isinstance(body, dict):
        return None
    result = body.get("result") or {}
    if isinstance(result, list):
        result = result[0] if result else {}
    text = result.get("text", "") or ""
    utts = result.get("utterances") or []
    definite = any(u.get("definite") for u in utts) if utts else False
    return text, definite


async def doubao_relay(req):
    """浏览器 <-> 本服务 <-> 火山引擎大模型流式识别。协议对浏览器侧与讯飞中转完全一致。"""
    ws_client = web.WebSocketResponse(heartbeat=20)
    await ws_client.prepare(req)

    appid = req.query.get("appid") or ENV_DB_APPID
    token = req.query.get("token") or ENV_DB_TOKEN
    resource = req.query.get("resource") or ENV_DB_RESOURCE
    if not appid or not token:
        await ws_client.send_json({"type": "error", "msg": "缺少豆包 App ID / Access Token"})
        await ws_client.close()
        return ws_client

    headers = {
        "X-Api-App-Key": appid,
        "X-Api-Access-Key": token,
        "X-Api-Resource-Id": resource,
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }

    # 时长版和并发版哪个开通了用哪个：逐个尝试，403 就换下一个
    resources = []
    for r in (resource, "volc.bigasr.sauc.duration", "volc.bigasr.sauc.concurrent"):
        if r and r not in resources:
            resources.append(r)

    session = aiohttp.ClientSession()
    ws_db, errors = None, []
    for res_id in resources:
        headers["X-Api-Resource-Id"] = res_id
        headers["X-Api-Connect-Id"] = str(uuid.uuid4())
        try:
            ws_db = await session.ws_connect("wss://" + DB_HOST + DB_PATH,
                                             headers=headers, heartbeat=20, timeout=30)
            break
        except aiohttp.WSServerHandshakeError as e:
            hint = (e.headers.get("X-Api-Message", "") if e.headers else "") or e.message
            errors.append(f"[{res_id.rsplit('.',1)[-1]}] HTTP {e.status} {hint}")
        except Exception as e:
            errors.append(f"[{res_id.rsplit('.',1)[-1]}] {type(e).__name__} {str(e)[:80]}")
    if ws_db is None:
        msg = ("无法连接豆包识别（时长版/并发版都试过）：" + "；".join(errors))[:300]
        msg += "。请到火山控制台确认：1)已开通「流式语音识别大模型」并领取免费额度 2)Access Token 复制完整"
        await ws_client.send_json({"type": "error", "msg": msg})
        await ws_client.close()
        await session.close()
        return ws_client

    # ── 从这里开始改为「按需会话」模型 ──────────────────────────────
    # 豆包的识别会话闲置若干秒就会过期（Jake 说话时我们不发音频，正好触发）。
    # 因此不再"一条会话打到底"，而是：有音频来、没会话就现开一个；
    # 闲置 8 秒主动关掉（省时长计费）；会话中途过期就静默重开，绝不因此掉备用。
    working_res = headers["X-Api-Resource-Id"]      # 刚才验证成功的资源版本，之后复用
    await ws_db.close()                              # 验证用的连接直接关掉，按需再开
    ws_db = None

    PARAMS = {
        "user": {"uid": "jake"},
        "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1},
        "request": {
            "model_name": "bigmodel",
            "enable_punc": True,      # 标点
            "enable_itn": True,       # 数字/时间规整
            "show_utterances": True,  # 返回分句，带 definite 终判
            "result_type": "single",  # 每次只回当前分句，前端逐句累加
        },
    }
    IDLE_CLOSE_SEC = 8.0   # 闲置这么久就主动收掉会话

    st = {"ws": None, "seq": 0, "gen": 0, "last_audio": 0.0, "reader": None}

    async def close_session():
        ws, st["ws"] = st["ws"], None
        reader, st["reader"] = st["reader"], None
        if reader:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    async def read_session(ws, gen):
        """读一条会话的识别结果；会话过期类错误只静默收场，等下一段音频重开。"""
        try:
            async for msg in ws:
                if gen != st["gen"]:
                    return
                if msg.type != aiohttp.WSMsgType.BINARY:
                    continue
                parsed = _parse(msg.data)
                if not parsed:
                    continue
                if parsed["type"] == SERVER_ERROR_RESPONSE:
                    emsg = ""
                    if isinstance(parsed["body"], dict):
                        emsg = parsed["body"].get("message") or json.dumps(parsed["body"], ensure_ascii=False)
                    print("[doubao] 会话内错误（将自动重开）:", (emsg or parsed["code"]))
                    if gen == st["gen"]:
                        st["ws"] = None   # 标记失效，下段音频会重开
                    return
                res = _extract(parsed["body"])
                if res and res[0]:
                    with contextlib.suppress(Exception):
                        await ws_client.send_json({"type": "asr", "text": res[0], "final": res[1]})
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print("[doubao] 读取异常（将自动重开）:", e)
            if gen == st["gen"]:
                st["ws"] = None

    async def ensure_session():
        if st["ws"] is not None and not st["ws"].closed:
            return True
        await close_session()
        h = {"X-Api-App-Key": appid, "X-Api-Access-Key": token,
             "X-Api-Resource-Id": working_res, "X-Api-Connect-Id": str(uuid.uuid4())}
        try:
            ws = await session.ws_connect("wss://" + DB_HOST + DB_PATH,
                                          headers=h, heartbeat=20, timeout=15)
            st["gen"] += 1
            st["seq"] = 1
            await ws.send_bytes(_full_client_request(st["seq"], PARAMS))
            st["ws"] = ws
            st["reader"] = asyncio.create_task(read_session(ws, st["gen"]))
            return True
        except Exception as e:
            print("[doubao] 重开会话失败:", e)
            return False

    async def idle_watch():
        """闲置看门狗：Jake 说话/思考期间没有音频，主动收掉会话，避免过期报错也省时长。"""
        while True:
            await asyncio.sleep(2)
            if st["ws"] is not None and st["last_audio"] and \
               (asyncio.get_event_loop().time() - st["last_audio"]) > IDLE_CLOSE_SEC:
                await close_session()

    await ws_client.send_json({"type": "ready"})

    async def pump_up():
        """浏览器 PCM → 火山：每段发声一条会话，按需开、闲置关、坏了重开。"""
        fail_streak = 0
        rx_bytes = 0
        last_dbg = 0.0
        async for msg in ws_client:
            if msg.type == aiohttp.WSMsgType.BINARY:
                if not msg.data:
                    continue
                rx_bytes += len(msg.data)
                st["last_audio"] = asyncio.get_event_loop().time()
                if st["last_audio"] - last_dbg > 1.0:
                    last_dbg = st["last_audio"]
                    with contextlib.suppress(Exception):
                        await ws_client.send_json({"type": "debug", "msg": f"server received audio {rx_bytes} bytes"})
                if not await ensure_session():
                    fail_streak += 1
                    if fail_streak >= 20:   # 连续 ~5 秒都开不出会话才真放弃
                        await ws_client.send_json({"type": "error", "msg": "豆包会话反复无法建立，请检查网络或额度"})
                        return
                    continue
                fail_streak = 0
                st["seq"] += 1
                try:
                    await st["ws"].send_bytes(_audio_request(st["seq"], bytes(msg.data), last=False))
                except Exception:
                    st["ws"] = None        # 发送失败：标记失效，下一包重开
            elif msg.type == aiohttp.WSMsgType.TEXT and msg.data == "stop":
                with contextlib.suppress(Exception):
                    await ws_client.send_json({"type": "debug", "msg": "server received stop"})
                if st["ws"] is not None and not st["ws"].closed:
                    st["seq"] += 1
                    with contextlib.suppress(Exception):
                        await st["ws"].send_bytes(_audio_request(st["seq"], b"", last=True))
                    # 豆包的最终结果可能在结束帧后晚到；过早关掉 reader 会让前端永远拿不到最后一句。
                    await asyncio.sleep(2.4)
                await close_session()
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                return

    up = asyncio.create_task(pump_up())
    watch = asyncio.create_task(idle_watch())
    await asyncio.wait([up], return_when=asyncio.FIRST_COMPLETED)
    watch.cancel()
    await asyncio.gather(up, watch, return_exceptions=True)
    await close_session()
    for closer in (session.close(), ws_client.close()):
        with contextlib.suppress(Exception):
            await closer
    return ws_client
