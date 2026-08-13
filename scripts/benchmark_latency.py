#!/usr/bin/env python3
"""Exercise 30 real local Chat + streaming-TTS turns and store metrics only."""

import argparse
import asyncio
import codecs
import json
import re
import time
from pathlib import Path

import aiohttp


SHORT = [
    "That makes sense.",
    "I agree with you.",
    "It was really helpful.",
    "I work in Shenzhen.",
    "My daughter loves it.",
    "We finished it yesterday.",
    "I prefer the first one.",
    "The meeting starts tomorrow.",
    "I have already tried it.",
    "我下周要去香港见客户。",
]
MEDIUM = [
    "My company makes smart gardening products for small apartments.",
    "I travel to Hong Kong twice a week for client meetings.",
    "We are building a modular system that is easier to maintain.",
    "The main reason is that our overseas clients asked for it.",
    "I started learning English because I want to speak more naturally.",
    "Our team tested the new design with three different customers.",
    "I usually think for a moment before I finish a long sentence.",
    "The product is useful for people who do not have a large garden.",
    "We changed the plan after receiving feedback from the sales team.",
    "我想用更自然的英语向海外客户介绍我们的产品。",
]
PAUSED = [
    "My company mainly focuses on small gardens, and after thinking about it, we may expand overseas.",
    "The reason is because, well, customers want something easier to install.",
    "We are currently developing a modular product, and the next step is field testing.",
    "I would like to talk about plant therapy, because I have been reading about it recently.",
    "Our next product will probably be smaller, although we have not finalized the design.",
    "The customer asked for a new feature, and after a short pause we agreed to test it.",
    "We have been working with overseas clients, so I want my English to sound more natural.",
    "The most difficult part is explaining the idea clearly when I need a moment to think.",
    "因为我们现在还在测试，所以我想先看看客户的真实反馈。",
    "然后下一步我们会联系海外客户，不过具体时间还没有定。",
]
CASES = [("short", value) for value in SHORT]
CASES += [("medium", value) for value in MEDIUM]
CASES += [("paused", value) for value in PAUSED]
SYSTEM = (
    "You are Jake in a live English conversation. Reply in one or two natural sentences. "
    "Start with an emotion tag such as [neutral], then answer directly without stage directions."
)
EMOTION = re.compile(r"^\s*\[(?:happy|sad|angry|fearful|surprised|neutral)\]\s*", re.I)
SOFT = re.compile(r"[,;:.!?，。！？；：…][\"')」』]?\s*$")
SENTENCE = re.compile(r"[.!?。！？][\"')」』]?\s*$")


def first_segment(buffer, final=False):
    clean = EMOTION.sub("", buffer, count=1)
    if len(clean.strip()) >= 10 and SOFT.search(clean):
        return clean.strip()
    if len(clean) >= 18 and SENTENCE.search(clean):
        return clean.strip()
    if len(clean) >= 48 and not re.search(r"\[\s*echo\s*:", clean, re.I):
        cut = clean.rfind(" ", 0, 49)
        if cut >= 24:
            return clean[:cut].strip()
    if final and clean.strip():
        return clean.strip()
    return ""


async def receive_ready(ws):
    while True:
        message = await ws.receive(timeout=15)
        if message.type == aiohttp.WSMsgType.TEXT:
            payload = json.loads(message.data)
            if payload.get("type") == "ready":
                return
            if payload.get("type") == "error":
                raise RuntimeError("streaming TTS rejected preconnect")
        elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            raise RuntimeError("streaming TTS closed before ready")


async def run_turn(session, base_url, index, category, utterance):
    started = time.perf_counter()
    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/tts"
    ws_task = asyncio.create_task(session.ws_connect(ws_url, heartbeat=20))
    chat_task = asyncio.create_task(session.post(
        base_url + "/api/chat",
        json={"turn_id": f"benchmark-{index}", "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": utterance},
        ]},
    ))
    ws, response = await asyncio.gather(ws_task, chat_task)
    if response.status != 200:
        response.release()
        await ws.close()
        raise RuntimeError(f"chat returned HTTP {response.status}")

    tts_ready = None
    first_raw = None
    first_speakable = None
    tts_requested = None
    chat_completed = None

    async def wait_for_tts_ready():
        nonlocal tts_ready
        await receive_ready(ws)
        tts_ready = time.perf_counter()

    ready_task = asyncio.create_task(wait_for_tts_ready())

    async def consume_chat():
        nonlocal first_raw, first_speakable, tts_requested, chat_completed
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        async for chunk in response.content.iter_any():
            now = time.perf_counter()
            if chunk and first_raw is None:
                first_raw = now
            buffer += decoder.decode(chunk)
            segment = first_segment(buffer)
            if segment and first_speakable is None:
                first_speakable = now
                await ready_task
                await ws.send_json({
                    "type": "begin_reply", "reply_id": f"benchmark-{index}",
                    "voice": "English_magnetic_voiced_man", "model": "speech-2.8-turbo",
                    "language_boost": "Chinese" if re.search(r"[\u4e00-\u9fff]", segment) else "English",
                    "emotion": "neutral",
                })
                await ws.send_json({
                    "type": "speak", "reply_id": f"benchmark-{index}",
                    "segment_id": 0, "text": segment,
                })
                tts_requested = time.perf_counter()
        buffer += decoder.decode(b"", final=True)
        if first_speakable is None:
            segment = first_segment(buffer, final=True)
            if segment:
                first_speakable = time.perf_counter()
                await ready_task
                await ws.send_json({
                    "type": "begin_reply", "reply_id": f"benchmark-{index}",
                    "voice": "English_magnetic_voiced_man", "model": "speech-2.8-turbo",
                    "language_boost": "Chinese" if re.search(r"[\u4e00-\u9fff]", segment) else "English",
                    "emotion": "neutral",
                })
                await ws.send_json({
                    "type": "speak", "reply_id": f"benchmark-{index}",
                    "segment_id": 0, "text": segment,
                })
                tts_requested = time.perf_counter()
        chat_completed = time.perf_counter()

    chat_reader = asyncio.create_task(consume_chat())
    await ready_task
    first_audio = None
    stream_completed = None
    segment_finished = False
    try:
        while True:
            message = await ws.receive(timeout=20)
            now = time.perf_counter()
            if message.type == aiohttp.WSMsgType.BINARY and first_audio is None:
                first_audio = now
            elif message.type == aiohttp.WSMsgType.TEXT:
                payload = json.loads(message.data)
                if payload.get("type") == "segment_finished" and not segment_finished:
                    segment_finished = True
                    await ws.send_json({"type": "end_reply", "reply_id": f"benchmark-{index}"})
                elif payload.get("type") == "reply_finished":
                    stream_completed = now
                    break
                elif payload.get("type") == "error":
                    raise RuntimeError("streaming TTS returned an error")
            elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                raise RuntimeError("streaming TTS closed before completion")
        await chat_reader
    finally:
        if not chat_reader.done():
            chat_reader.cancel()
            await asyncio.gather(chat_reader, return_exceptions=True)
        if not ready_task.done():
            ready_task.cancel()
            await asyncio.gather(ready_task, return_exceptions=True)
        response.release()
        await ws.close()

    if None in (first_raw, first_speakable, tts_requested, first_audio, chat_completed, stream_completed):
        raise RuntimeError("turn completed without all latency markers")
    milliseconds = lambda end, begin=started: round((end - begin) * 1000, 1)
    return {
        "type": "benchmark_turn", "turn_index": index, "category": category, "success": True,
        "tts_ready_ms": milliseconds(tts_ready),
        "chat_first_raw_ms": milliseconds(first_raw),
        "chat_first_speakable_ms": milliseconds(first_speakable),
        "chat_complete_ms": milliseconds(chat_completed),
        "tts_start_to_first_audio_ms": milliseconds(first_audio, tts_requested),
        "pipeline_first_audio_ms": milliseconds(first_audio),
        "tts_stream_complete_ms": milliseconds(stream_completed, tts_requested),
    }


async def benchmark(base_url, output):
    timeout = aiohttp.ClientTimeout(total=60, sock_connect=10, sock_read=30)
    connector = aiohttp.TCPConnector(limit=4, limit_per_host=4, ttl_dns_cache=300)
    rows = []
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        await run_turn(session, base_url, 0, "warmup", SHORT[0])
        for index, (category, utterance) in enumerate(CASES, 1):
            try:
                row = await run_turn(session, base_url, index, category, utterance)
            except Exception as error:
                row = {
                    "type": "benchmark_turn", "turn_index": index, "category": category,
                    "success": False, "failure_type": type(error).__name__,
                }
            rows.append(row)
            status = "ok" if row["success"] else "failed"
            print(f"turn {index:02d}/30 {category}: {status}", flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")
    return 0 if all(row["success"] for row in rows) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8800")
    parser.add_argument("--output", type=Path, default=Path("latency-logs/pr3-benchmark.jsonl"))
    args = parser.parse_args()
    return asyncio.run(benchmark(args.base_url.rstrip("/"), args.output))


if __name__ == "__main__":
    raise SystemExit(main())
