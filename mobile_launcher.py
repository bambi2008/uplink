"""Launch Uplink behind a private or temporary HTTPS phone gateway."""

import json
import argparse
import os
import pathlib
import queue
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser


ROOT = pathlib.Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("UPLINK_MOBILE_PORT", "8810"))
HEALTH_URL = f"http://{HOST}:{PORT}/api/diag"
TOKEN_PATH = ROOT / "mobile-access.json"
SERVER_LOG = ROOT / f"server-mobile-{PORT}.log"
CLOUDFLARED_LOG = ROOT / "mobile-cloudflared.log"
PUBLIC_URL_RE = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com", re.I)
TAILSCALE_URL_RE = re.compile(r"https://[-a-z0-9.]+\.ts\.net", re.I)


def load_or_create_token(path=TOKEN_PATH):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        token = str(payload.get("access_token") or "")
        if len(token) >= 32:
            return token
    except (OSError, ValueError, TypeError):
        pass
    token = secrets.token_urlsafe(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"access_token": token}, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return token


def probe():
    request = urllib.request.Request(HEALTH_URL, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return json.loads(response.read().decode("utf-8")) if response.status == 200 else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def stop_existing_server(status):
    if not status or status.get("app") != "uplink" or not status.get("pid"):
        return True
    try:
        os.kill(int(status["pid"]), signal.SIGTERM)
    except OSError:
        return False
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if probe() is None:
            return True
        time.sleep(0.2)
    return False


def start_server(token):
    env = os.environ.copy()
    env.update({
        "UPLINK_HOST": HOST,
        "UPLINK_PORT": str(PORT),
        "UPLINK_MOBILE_MODE": "1",
        "UPLINK_MOBILE_ACCESS_TOKEN": token,
    })
    log = SERVER_LOG.open("a", encoding="utf-8")
    kwargs = {"cwd": str(ROOT), "env": env, "stdin": subprocess.DEVNULL,
              "stdout": log, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen([sys.executable, "-u", str(ROOT / "server.py")], **kwargs)
    log.close()
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        status = probe()
        if status and (status.get("mobile") or {}).get("mode"):
            return process
        if process.poll() is not None:
            break
        time.sleep(0.4)
    process.terminate()
    raise RuntimeError(f"手机服务启动失败，请查看 {SERVER_LOG.name}")


def cloudflared_path():
    name = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    local = ROOT / ".tools" / name
    if local.exists():
        return local
    from shutil import which
    found = which("cloudflared")
    return pathlib.Path(found) if found else None


def start_quick_tunnel(binary):
    process = subprocess.Popen(
        [str(binary), "tunnel", "--url", f"http://{HOST}:{PORT}", "--no-autoupdate"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    lines = queue.Queue()

    def read_output():
        with CLOUDFLARED_LOG.open("a", encoding="utf-8") as log:
            for line in process.stdout:
                log.write(line)
                log.flush()
                lines.put(line)

    threading.Thread(target=read_output, daemon=True).start()
    deadline = time.monotonic() + 45
    recent = []
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            continue
        recent.append(line.strip())
        match = PUBLIC_URL_RE.search(line)
        if match:
            return process, match.group(0)
    process.terminate()
    detail = "\n".join(recent[-8:])
    raise RuntimeError("HTTPS 手机入口建立失败。" + ("\n" + detail if detail else ""))


def tailscale_path():
    from shutil import which
    found = which("tailscale")
    if found:
        return pathlib.Path(found)
    if os.name == "nt":
        candidate = pathlib.Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale" / "tailscale.exe"
        if candidate.exists():
            return candidate
    return None


def start_tailscale_serve(binary):
    process = subprocess.Popen(
        [str(binary), "serve", f"http://{HOST}:{PORT}"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    lines = queue.Queue()

    def read_output():
        for line in process.stdout:
            lines.put(line)

    threading.Thread(target=read_output, daemon=True).start()
    deadline = time.monotonic() + 90
    recent = []
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            continue
        recent.append(line.strip())
        match = TAILSCALE_URL_RE.search(line)
        if match:
            return process, match.group(0)
    process.terminate()
    hint = "请先在电脑和手机安装 Tailscale，并登录同一个账户。"
    output = "\n".join(recent[-10:])
    raise RuntimeError("固定手机入口建立失败。" + hint + ("\n" + output if output else ""))


def open_pairing_page(pairing_url):
    import segno

    folder = pathlib.Path(tempfile.gettempdir()) / "uplink-mobile"
    folder.mkdir(exist_ok=True)
    qr_path = folder / "pairing.svg"
    page_path = folder / "pairing.html"
    segno.make(pairing_url, error="m").save(qr_path, scale=8, border=2,
                                            dark="#05070c", light="#e9eef2")
    escaped = pairing_url.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
    page_path.write_text(f"""<!doctype html><meta charset=\"utf-8\"><title>Uplink 手机配对</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#05070c;color:#e9eef2;font-family:Arial,sans-serif}}
main{{width:min(520px,calc(100% - 40px));text-align:center}}img{{width:min(330px,80vw);background:#e9eef2;padding:12px;border-radius:8px}}
h1{{font-size:26px}}p{{color:#8fa3b0;line-height:1.7}}a{{color:#e8a254;overflow-wrap:anywhere}}</style>
<main><h1>Uplink 手机端</h1><p>用 iPhone 相机或安卓扫码打开。首次打开允许麦克风，然后可添加到主屏幕。</p>
<img src=\"{qr_path.as_uri()}\" alt=\"Uplink 手机配对二维码\"><p><a href=\"{escaped}\">{escaped}</a></p>
<p>保持“启动手机端”窗口运行；关闭窗口即断开手机入口。</p></main>""", encoding="utf-8")
    webbrowser.open(page_path.as_uri())


def stop_process(process):
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Launch the Uplink phone gateway")
    parser.add_argument("--gateway", choices=("quick", "tailscale"), default="quick")
    args = parser.parse_args(argv)
    binary = tailscale_path() if args.gateway == "tailscale" else cloudflared_path()
    if not binary:
        name = "Tailscale" if args.gateway == "tailscale" else "cloudflared"
        print(f"[Uplink] {name} 未安装。", file=sys.stderr)
        return 1
    existing = probe()
    if existing and not stop_existing_server(existing):
        print(f"[Uplink] 端口 {PORT} 上的旧手机服务无法关闭，请重启电脑后再试。", file=sys.stderr)
        return 1

    token = load_or_create_token()
    server_process = tunnel_process = None
    try:
        print("[Uplink] 正在启动手机安全服务...")
        server_process = start_server(token)
        print("[Uplink] 正在建立 HTTPS 手机入口，通常需要 5-20 秒...")
        if args.gateway == "tailscale":
            tunnel_process, public_url = start_tailscale_serve(binary)
        else:
            tunnel_process, public_url = start_quick_tunnel(binary)
        pairing_url = public_url.rstrip("/") + "/?pair=" + urllib.parse.quote(token)
        open_pairing_page(pairing_url)
        label = "固定私密地址" if args.gateway == "tailscale" else "临时测试地址"
        print(f"\n[Uplink] 手机入口已建立（{label}），二维码已在浏览器打开。")
        print("[Uplink] 保持本窗口运行；按 Ctrl+C 可安全关闭。\n")
        while tunnel_process.poll() is None and server_process.poll() is None:
            time.sleep(1)
        raise RuntimeError("手机连接进程意外退出，请重新启动。")
    except KeyboardInterrupt:
        print("\n[Uplink] 手机入口已关闭。")
        return 0
    except Exception as exc:
        print(f"[Uplink] {exc}", file=sys.stderr)
        return 1
    finally:
        stop_process(tunnel_process)
        stop_process(server_process)


if __name__ == "__main__":
    raise SystemExit(main())
