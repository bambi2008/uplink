"""Commercial multi-user runtime for Uplink.

This module is deliberately isolated from the desktop runtime. It is enabled
only when UPLINK_COMMERCIAL_MODE=1 and never reads or writes provider secrets.
"""

import asyncio
import contextlib
import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import re
import secrets
import sqlite3
import time
import uuid
from collections import defaultdict, deque

from aiohttp import web


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


ENABLED = env_flag("UPLINK_COMMERCIAL_MODE", False)
ALLOW_SIGNUP = env_flag("UPLINK_ALLOW_SIGNUP", True)
INSECURE_COOKIE = env_flag("UPLINK_INSECURE_COOKIE", False)
SESSION_DAYS = max(1, int(os.environ.get("UPLINK_SESSION_DAYS", "30")))
DAILY_REQUEST_LIMIT = max(20, int(os.environ.get("UPLINK_DAILY_REQUEST_LIMIT", "500")))
DATA_DIR = pathlib.Path(os.environ.get("UPLINK_DATA_DIR", pathlib.Path(__file__).parent / "commercial-data"))
DB_PATH = DATA_DIR / "uplink.sqlite3"
COOKIE_NAME = "uplink_session"
if hasattr(web, "RequestKey"):
    USER_KEY = web.RequestKey("uplink_commercial_user", dict)
else:
    USER_KEY = "uplink_commercial_user"
STORE_KEY = web.AppKey("uplink_commercial_store", object)
PUBLIC_ORIGIN = os.environ.get("UPLINK_PUBLIC_ORIGIN", "").rstrip("/")
TRUST_PROXY = env_flag("UPLINK_TRUST_PROXY", False)
NATIVE_ORIGINS = {
    value.strip().rstrip("/") for value in os.environ.get(
        "UPLINK_NATIVE_ORIGINS", "capacitor://localhost,https://localhost"
    ).split(",") if value.strip()
}
AUTH_WINDOW_SECONDS = 600
AUTH_ATTEMPT_LIMIT = 20
AUTH_ATTEMPTS = defaultdict(deque)
WS_TICKET_SECONDS = 30
WS_TICKETS = {}

PUBLIC_PATHS = {
    "/", "/favicon.ico", "/manifest.webmanifest", "/service-worker.js",
    "/api/diag", "/api/health/live", "/api/health/ready",
    "/api/account/register", "/api/account/login",
}
METERED_PATHS = {
    "/api/chat", "/api/tts", "/api/briefing", "/api/report", "/api/ise",
    "/ws/asr", "/ws/tts", "/ws/doubao",
}
WEBSOCKET_PATHS = {"/ws/asr", "/ws/tts", "/ws/doubao"}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + digest.hex()


def _password_valid(password, encoded):
    try:
        algorithm, salt_hex, expected = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        actual = _password_hash(password, bytes.fromhex(salt_hex)).rsplit("$", 1)[1]
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _request_session_token(req):
    auth = req.headers.get("Authorization", "")
    if isinstance(auth, str) and auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
        if supplied:
            return supplied
    return req.cookies.get(COOKIE_NAME, "")


def _native_client(req):
    return req.headers.get("X-Uplink-Client", "").strip().lower() == "native"


def _issue_ws_ticket(user_id, path):
    now = time.monotonic()
    expired = [key for key, value in WS_TICKETS.items() if value[2] <= now]
    for key in expired:
        WS_TICKETS.pop(key, None)
    ticket = secrets.token_urlsafe(24)
    WS_TICKETS[_token_hash(ticket)] = (user_id, path, now + WS_TICKET_SECONDS)
    return ticket


def _consume_ws_ticket(ticket, path):
    if not ticket:
        return None
    record = WS_TICKETS.pop(_token_hash(ticket), None)
    if not record:
        return None
    user_id, allowed_path, expires = record
    if allowed_path != path or expires <= time.monotonic():
        return None
    return user_id


def _public_user(row, used=0):
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "plan": row["plan"],
        "usage": {"used": used, "limit": DAILY_REQUEST_LIMIT},
    }


class CommercialStore:
    def __init__(self, path=None):
        self.path = pathlib.Path(path or DB_PATH)
        self.lock = asyncio.Lock()

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    async def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.lock:
            with contextlib.closing(self.connect()) as db, db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        plan TEXT NOT NULL DEFAULT 'starter',
                        preferences TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        expires_at TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS daily_usage (
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        usage_date TEXT NOT NULL,
                        request_count INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (user_id, usage_date)
                    );
                    CREATE TABLE IF NOT EXISTS reports (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        report TEXT NOT NULL,
                        transcript TEXT NOT NULL DEFAULT '',
                        duration_minutes INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS reports_user_created
                    ON reports(user_id, created_at DESC);
                """)

    async def register(self, email, password, name):
        email = str(email or "").strip().lower()
        name = str(name or "").strip()[:60]
        if not EMAIL_RE.match(email):
            raise ValueError("请输入有效邮箱")
        if len(password or "") < 8:
            raise ValueError("密码至少需要 8 位")
        if not name:
            raise ValueError("请输入称呼")
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        user_id = str(uuid.uuid4())
        async with self.lock:
            try:
                with contextlib.closing(self.connect()) as db, db:
                    db.execute(
                        "INSERT INTO users(id,email,name,password_hash,created_at) VALUES(?,?,?,?,?)",
                        (user_id, email, name, await asyncio.to_thread(_password_hash, password), now),
                    )
            except sqlite3.IntegrityError as exc:
                raise ValueError("这个邮箱已经注册") from exc
        return await self.user_by_id(user_id)

    async def authenticate(self, email, password):
        async with self.lock:
            with contextlib.closing(self.connect()) as db, db:
                row = db.execute("SELECT * FROM users WHERE email=?", (str(email or "").strip().lower(),)).fetchone()
        if not row or not await asyncio.to_thread(_password_valid, password or "", row["password_hash"]):
            return None
        return dict(row)

    async def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        now = dt.datetime.now(dt.timezone.utc)
        expires = now + dt.timedelta(days=SESSION_DAYS)
        async with self.lock:
            with contextlib.closing(self.connect()) as db, db:
                db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),))
                db.execute(
                    "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
                    (_token_hash(token), user_id, expires.isoformat(), now.isoformat()),
                )
        return token

    async def user_for_token(self, token):
        if not token:
            return None
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        async with self.lock:
            with contextlib.closing(self.connect()) as db, db:
                row = db.execute(
                    "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                    "WHERE s.token_hash=? AND s.expires_at>?",
                    (_token_hash(token), now),
                ).fetchone()
        return dict(row) if row else None

    async def delete_session(self, token):
        if not token:
            return
        async with self.lock:
            with contextlib.closing(self.connect()) as db, db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))

    async def user_by_id(self, user_id):
        async with self.lock:
            with contextlib.closing(self.connect()) as db, db:
                row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    async def usage(self, user_id):
        today = dt.date.today().isoformat()
        async with self.lock:
            with contextlib.closing(self.connect()) as db, db:
                row = db.execute(
                    "SELECT request_count FROM daily_usage WHERE user_id=? AND usage_date=?",
                    (user_id, today),
                ).fetchone()
        return int(row[0]) if row else 0

    async def consume(self, user_id):
        today = dt.date.today().isoformat()
        async with self.lock:
            with contextlib.closing(self.connect()) as db, db:
                row = db.execute(
                    "SELECT request_count FROM daily_usage WHERE user_id=? AND usage_date=?",
                    (user_id, today),
                ).fetchone()
                used = int(row[0]) if row else 0
                if used >= DAILY_REQUEST_LIMIT:
                    return False, used
                db.execute(
                    "INSERT INTO daily_usage(user_id,usage_date,request_count) VALUES(?,?,1) "
                    "ON CONFLICT(user_id,usage_date) DO UPDATE SET request_count=request_count+1",
                    (user_id, today),
                )
        return True, used + 1

    async def preferences(self, user_id):
        user = await self.user_by_id(user_id)
        try:
            value = json.loads(user.get("preferences") or "{}") if user else {}
        except json.JSONDecodeError:
            value = {}
        return value if isinstance(value, dict) else {}

    async def save_preferences(self, user_id, value):
        async with self.lock:
            with contextlib.closing(self.connect()) as db, db:
                db.execute("UPDATE users SET preferences=? WHERE id=?", (json.dumps(value), user_id))

    async def save_report(self, user_id, report, transcript="", duration_minutes=0):
        created = dt.datetime.now(dt.timezone.utc).isoformat()
        report_id = str(uuid.uuid4())
        async with self.lock:
            with contextlib.closing(self.connect()) as db, db:
                db.execute(
                    "INSERT INTO reports(id,user_id,report,transcript,duration_minutes,created_at) VALUES(?,?,?,?,?,?)",
                    (report_id, user_id, report, transcript, int(duration_minutes or 0), created),
                )
        return report_id

    async def reports(self, user_id, limit=50):
        async with self.lock:
            with contextlib.closing(self.connect()) as db, db:
                rows = db.execute(
                    "SELECT id,report,duration_minutes,created_at FROM reports "
                    "WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                    (user_id, min(max(int(limit), 1), 100)),
                ).fetchall()
        return [dict(row) for row in rows]


def store(req):
    return req.app[STORE_KEY]


def user(req):
    return req.get(USER_KEY)


def _set_session_cookie(response, token):
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_DAYS * 86400, httponly=True,
        secure=not INSECURE_COOKIE, samesite="Strict", path="/",
    )


def _request_ip(req):
    if TRUST_PROXY:
        forwarded = req.headers.get("CF-Connecting-IP") or req.headers.get("X-Forwarded-For", "").split(",", 1)[0]
        if forwarded:
            return forwarded.strip()[:64]
    return str(req.remote or "unknown")[:64]


def _auth_attempt_allowed(req):
    now = time.monotonic()
    attempts = AUTH_ATTEMPTS[_request_ip(req)]
    while attempts and attempts[0] <= now - AUTH_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= AUTH_ATTEMPT_LIMIT:
        return False
    attempts.append(now)
    return True


async def account_register(req):
    if not _auth_attempt_allowed(req):
        return web.json_response({"error": "尝试次数过多，请稍后再试"}, status=429)
    if not ALLOW_SIGNUP:
        return web.json_response({"error": "当前仅支持邀请注册"}, status=403)
    try:
        body = await req.json()
        account = await store(req).register(body.get("email"), body.get("password"), body.get("name"))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return web.json_response({"error": str(exc) or "注册信息无效"}, status=400)
    token = await store(req).create_session(account["id"])
    payload = {"user": _public_user(account)}
    if _native_client(req):
        payload["access_token"] = token
    response = web.json_response(payload, headers={"Cache-Control": "no-store"})
    _set_session_cookie(response, token)
    return response


async def account_login(req):
    if not _auth_attempt_allowed(req):
        return web.json_response({"error": "尝试次数过多，请稍后再试"}, status=429)
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "登录信息无效"}, status=400)
    account = await store(req).authenticate(body.get("email"), body.get("password"))
    if not account:
        return web.json_response({"error": "邮箱或密码不正确"}, status=401)
    token = await store(req).create_session(account["id"])
    used = await store(req).usage(account["id"])
    payload = {"user": _public_user(account, used)}
    if _native_client(req):
        payload["access_token"] = token
    response = web.json_response(payload, headers={"Cache-Control": "no-store"})
    _set_session_cookie(response, token)
    return response


async def account_logout(req):
    await store(req).delete_session(_request_session_token(req))
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE_NAME, path="/")
    return response


async def account_me(req):
    account = user(req)
    used = await store(req).usage(account["id"])
    return web.json_response({"user": _public_user(account, used)})


async def websocket_ticket(req):
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "请求体不是 JSON"}, status=400)
    path = str(body.get("path") or "") if isinstance(body, dict) else ""
    if path not in WEBSOCKET_PATHS:
        return web.json_response({"error": "不支持的语音通道"}, status=400)
    ticket = _issue_ws_ticket(user(req)["id"], path)
    return web.json_response({"ticket": ticket, "expires_in": WS_TICKET_SECONDS},
                             headers={"Cache-Control": "no-store"})


async def reports_list(req):
    return web.json_response({"reports": await store(req).reports(user(req)["id"])})


async def health_live(req):
    return web.json_response({"ok": True, "service": "uplink"})


async def health_ready(req):
    try:
        async with store(req).lock:
            with contextlib.closing(store(req).connect()) as db, db:
                db.execute("SELECT 1").fetchone()
        return web.json_response({"ok": True})
    except sqlite3.Error:
        return web.json_response({"ok": False}, status=503)


@web.middleware
async def auth_middleware(req, handler):
    if not ENABLED:
        return await handler(req)
    if req.path.startswith("/static/") or req.path in PUBLIC_PATHS:
        return await handler(req)

    if req.path not in PUBLIC_PATHS and PUBLIC_ORIGIN:
        origin = req.headers.get("Origin", "").rstrip("/")
        if origin and origin != PUBLIC_ORIGIN and origin not in NATIVE_ORIGINS:
            return web.json_response({"error": "请求来源无效"}, status=403)

    account = None
    supplied_ticket = req.query.get("ticket") if req.path in WEBSOCKET_PATHS else None
    if supplied_ticket:
        user_id = _consume_ws_ticket(supplied_ticket, req.path)
        if not user_id:
            return web.json_response({"error": "语音通道凭证无效", "code": "AUTH_REQUIRED"}, status=401)
        account = await store(req).user_by_id(user_id)
    else:
        account = await store(req).user_for_token(_request_session_token(req))
    if not account:
        return web.json_response({"error": "请先登录", "code": "AUTH_REQUIRED"}, status=401)
    req[USER_KEY] = account

    if req.path in METERED_PATHS:
        allowed, used = await store(req).consume(account["id"])
        if not allowed:
            return web.json_response({
                "error": "今日使用额度已用完，请明天继续或升级套餐",
                "code": "QUOTA_EXCEEDED", "usage": used, "limit": DAILY_REQUEST_LIMIT,
            }, status=429)
    return await handler(req)


def apply_cors_headers(req, response):
    """Attach native-app CORS headers before a response is prepared.

    The middleware runs too late for StreamResponse/WebSocketResponse objects
    whose headers are sent inside the handler.  server.prepare_response calls
    this helper at aiohttp's last safe pre-send hook as well.
    """
    if not ENABLED:
        return response
    origin = req.headers.get("Origin", "").rstrip("/")
    if origin in NATIVE_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Uplink-Client"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
    return response


@web.middleware
async def cors_middleware(req, handler):
    if not ENABLED:
        return await handler(req)
    origin = req.headers.get("Origin", "").rstrip("/")
    allowed = origin in NATIVE_ORIGINS
    if req.method == "OPTIONS" and allowed:
        response = web.Response(status=204)
    else:
        response = await handler(req)
    return apply_cors_headers(req, response)


async def store_context(app):
    runtime_store = CommercialStore()
    await runtime_store.initialize()
    app[STORE_KEY] = runtime_store
    yield


def add_routes(app):
    app.router.add_post("/api/account/register", account_register)
    app.router.add_post("/api/account/login", account_login)
    app.router.add_post("/api/account/logout", account_logout)
    app.router.add_get("/api/account/me", account_me)
    app.router.add_post("/api/account/ws-ticket", websocket_ticket)
    app.router.add_get("/api/reports", reports_list)
    app.router.add_get("/api/health/live", health_live)
    app.router.add_get("/api/health/ready", health_ready)
