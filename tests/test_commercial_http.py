import hashlib
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import commercial
import server


class CommercialHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = commercial.CommercialStore(pathlib.Path(self.temp.name) / "uplink.sqlite3")
        await self.store.initialize()
        self.enabled = patch.object(commercial, "ENABLED", True)
        self.insecure = patch.object(commercial, "INSECURE_COOKIE", True)
        self.password_hash = patch.object(
            commercial,
            "_password_hash",
            lambda password, salt=None: "scrypt$00$" + hashlib.sha256(password.encode("utf-8")).hexdigest(),
        )
        self.enabled.start()
        self.insecure.start()
        self.password_hash.start()
        commercial.AUTH_ATTEMPTS.clear()
        self.server_mode = patch.object(server, "COMMERCIAL_MODE", True)
        self.server_mode.start()

        app = web.Application(middlewares=[commercial.cors_middleware, commercial.auth_middleware])
        app[commercial.STORE_KEY] = self.store
        commercial.add_routes(app)
        app.router.add_get("/api/local_settings", server.api_local_settings)
        app.router.add_post("/api/local_settings", server.api_local_settings)

        async def private(req):
            return web.json_response({"user_id": commercial.user(req)["id"]})

        app.router.add_get("/api/private", private)
        app.router.add_get("/ws/asr", private)

        async def streamed(req):
            response = web.StreamResponse(headers={"Content-Type": "text/plain"})
            await response.prepare(req)
            await response.write(b"ok")
            await response.write_eof()
            return response

        app.router.add_get("/api/stream", streamed)
        app.on_response_prepare.append(server.prepare_response)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.insecure.stop()
        self.enabled.stop()
        self.password_hash.stop()
        self.server_mode.stop()
        commercial.AUTH_ATTEMPTS.clear()
        self.temp.cleanup()

    async def test_registration_cookie_and_logout_flow(self):
        denied = await self.client.get("/api/private")
        self.assertEqual(denied.status, 401)

        registered = await self.client.post("/api/account/register", json={
            "email": "customer@example.com", "password": "customer-password", "name": "Customer",
        })
        self.assertEqual(registered.status, 200)
        body = await registered.json()
        self.assertEqual(body["user"]["name"], "Customer")

        allowed = await self.client.get("/api/private")
        self.assertEqual(allowed.status, 200)
        self.assertEqual((await allowed.json())["user_id"], body["user"]["id"])

        logout = await self.client.post("/api/account/logout")
        self.assertEqual(logout.status, 200)
        denied_again = await self.client.get("/api/private")
        self.assertEqual(denied_again.status, 401)

    async def test_duplicate_registration_does_not_disclose_credentials(self):
        payload = {"email": "same@example.com", "password": "customer-password", "name": "Same"}
        self.assertEqual((await self.client.post("/api/account/register", json=payload)).status, 200)
        duplicate = await self.client.post("/api/account/register", json=payload)
        self.assertEqual(duplicate.status, 400)
        self.assertNotIn("password", str(await duplicate.json()).lower())

    async def test_customer_cannot_store_or_retrieve_provider_credentials(self):
        registered = await self.client.post("/api/account/register", json={
            "email": "settings@example.com", "password": "customer-password", "name": "Settings",
        })
        self.assertEqual(registered.status, 200)
        saved = await self.client.post("/api/local_settings", json={"settings": {
            "mm_key": "customer-must-not-control-this",
            "db_token": "customer-must-not-control-this",
            "user_name": "New Name",
            "mm_pace": "calm",
        }})
        self.assertEqual(saved.status, 200)
        restored = await self.client.get("/api/local_settings")
        payload = await restored.json()
        self.assertEqual(payload["settings"], {"user_name": "New Name", "mm_pace": "calm"})
        self.assertTrue(payload["managed_credentials"])
        self.assertNotIn("customer-must-not-control-this", repr(payload))

    async def test_auth_attempts_are_rate_limited(self):
        with patch.object(commercial, "AUTH_ATTEMPT_LIMIT", 2):
            first = await self.client.post("/api/account/login", json={"email": "x@example.com", "password": "wrong"})
            second = await self.client.post("/api/account/login", json={"email": "x@example.com", "password": "wrong"})
            blocked = await self.client.post("/api/account/login", json={"email": "x@example.com", "password": "wrong"})
        self.assertEqual(first.status, 401)
        self.assertEqual(second.status, 401)
        self.assertEqual(blocked.status, 429)

    async def test_native_bearer_session_and_one_time_websocket_ticket(self):
        registered = await self.client.post("/api/account/register", headers={"X-Uplink-Client": "native"}, json={
            "email": "native@example.com", "password": "customer-password", "name": "Native",
        })
        payload = await registered.json()
        token = payload.get("access_token")
        self.assertTrue(token)
        self.client.session.cookie_jar.clear()

        auth = {"Authorization": "Bearer " + token, "X-Uplink-Client": "native"}
        me = await self.client.get("/api/account/me", headers=auth)
        self.assertEqual(me.status, 200)
        ticket_response = await self.client.post("/api/account/ws-ticket", headers=auth, json={"path": "/ws/asr"})
        ticket = (await ticket_response.json())["ticket"]
        first = await self.client.get("/ws/asr?ticket=" + ticket)
        second = await self.client.get("/ws/asr?ticket=" + ticket, headers=auth)
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 401)

    async def test_native_cors_preflight_is_narrowly_allowed(self):
        allowed = await self.client.options("/api/private", headers={
            "Origin": "capacitor://localhost",
            "Access-Control-Request-Method": "GET",
        })
        denied = await self.client.options("/api/private", headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "GET",
        })
        self.assertEqual(allowed.status, 204)
        self.assertEqual(allowed.headers.get("Access-Control-Allow-Origin"), "capacitor://localhost")
        self.assertNotIn("Access-Control-Allow-Origin", denied.headers)

    async def test_native_cors_header_is_sent_before_stream_starts(self):
        registered = await self.client.post("/api/account/register", headers={"X-Uplink-Client": "native"}, json={
            "email": "stream@example.com", "password": "customer-password", "name": "Stream",
        })
        token = (await registered.json())["access_token"]
        streamed = await self.client.get("/api/stream", headers={
            "Authorization": "Bearer " + token,
            "X-Uplink-Client": "native",
            "Origin": "capacitor://localhost",
        })
        self.assertEqual(streamed.status, 200)
        self.assertEqual(await streamed.text(), "ok")
        self.assertEqual(streamed.headers.get("Access-Control-Allow-Origin"), "capacitor://localhost")

    async def test_production_origin_check_accepts_native_and_rejects_other_sites(self):
        registered = await self.client.post("/api/account/register", headers={"X-Uplink-Client": "native"}, json={
            "email": "origin@example.com", "password": "customer-password", "name": "Origin",
        })
        token = (await registered.json())["access_token"]
        auth = {"Authorization": "Bearer " + token, "X-Uplink-Client": "native"}
        with patch.object(commercial, "PUBLIC_ORIGIN", "https://app.example.com"):
            native = await self.client.get("/api/private", headers={**auth, "Origin": "capacitor://localhost"})
            attacker = await self.client.get("/api/private", headers={**auth, "Origin": "https://attacker.example"})
        self.assertEqual(native.status, 200)
        self.assertEqual(attacker.status, 403)


if __name__ == "__main__":
    unittest.main()
