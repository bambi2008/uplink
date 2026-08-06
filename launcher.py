"""Reliable local launcher for Uplink.

The launcher reuses a healthy server, waits for a newly started server to
answer its health endpoint, and keeps startup errors in one visible log file.
It intentionally uses only the Python standard library so dependency checks
can happen before importing server.py.
"""

import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser


ROOT = pathlib.Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("UPLINK_PORT", "8800"))
BASE_URL = f"http://{HOST}:{PORT}/"
HEALTH_URL = BASE_URL + "api/diag"
LOG_PATH = ROOT / f"server-{PORT}.log"
OPEN_BROWSER = os.environ.get("UPLINK_NO_BROWSER") != "1"


def expected_build():
    try:
        source = (ROOT / "server.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'^BUILD\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    return match.group(1) if match else ""


def probe():
    request = urllib.request.Request(HEALTH_URL, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def start_server():
    log = LOG_PATH.open("a", encoding="utf-8")
    kwargs = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    child = subprocess.Popen([sys.executable, "-u", str(ROOT / "server.py")], **kwargs)
    log.close()
    return child


def stop_outdated_server(status):
    """Stop only a positively identified older Uplink process."""
    if status.get("app") != "uplink":
        return False
    pid = status.get("pid")
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if probe() is None:
            return True
        time.sleep(0.2)
    return False


def tail_log(limit=1600):
    try:
        return LOG_PATH.read_text(encoding="utf-8", errors="replace")[-limit:].strip()
    except OSError:
        return ""


def main():
    existing = probe()
    if existing:
        wanted = expected_build()
        running = str(existing.get("build") or "")
        if wanted and running and running != wanted:
            print(f"Updating Uplink service: {running} -> {wanted}")
            if not stop_outdated_server(existing):
                print("An older Uplink service is still using port 8800. Restart the computer once, then launch again.", file=sys.stderr)
                return 1
        else:
            print(f"Uplink already running: {BASE_URL}")
            if OPEN_BROWSER:
                webbrowser.open(BASE_URL)
            return 0

    print("Starting Uplink service...")
    child = start_server()
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        status = probe()
        if status:
            print(f"Uplink ready: {BASE_URL}")
            if OPEN_BROWSER:
                webbrowser.open(BASE_URL)
            return 0
        if child.poll() is not None:
            break
        time.sleep(0.5)

    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
    print(f"Uplink failed to start. See: {LOG_PATH}", file=sys.stderr)
    details = tail_log()
    if details:
        print(details, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
