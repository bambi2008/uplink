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
        self.enabled.start()
        self.insecure.start()
        commercial.AUTH_ATTEMPTS.clear()
        self.server_mode = patch.object(server, "COMMERCIAL_MODE", True)
        self.server_mode.start()

        app = web.Application(middlewares=[commercial.auth_middleware])
        app[commercial.STORE_KEY] = self.store
        commercial.add_routes(app)
        app.router.add_get("/api/local_settings", server.api_local_settings)
        app.router.add_post("/api/local_settings", server.api_local_settings)

        async def private(req):
            return web.json_response({"user_id": commercial.user(req)["id"]})

        app.router.add_get("/api/private", private)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.insecure.stop()
        self.enabled.stop()
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


if __name__ == "__main__":
    unittest.main()
