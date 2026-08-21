import pathlib
import tempfile
import unittest
from unittest.mock import patch

import commercial
import server


class CommercialStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = commercial.CommercialStore(pathlib.Path(self.temp.name) / "uplink.sqlite3")
        await self.store.initialize()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_accounts_sessions_and_preferences_are_isolated(self):
        first = await self.store.register("first@example.com", "password-one", "First")
        second = await self.store.register("second@example.com", "password-two", "Second")
        self.assertNotEqual(first["id"], second["id"])

        token = await self.store.create_session(first["id"])
        signed_in = await self.store.user_for_token(token)
        self.assertEqual(signed_in["email"], "first@example.com")
        self.assertIsNone(await self.store.user_for_token("wrong-token"))

        await self.store.save_preferences(first["id"], {"mm_voice": "voice-a"})
        await self.store.save_preferences(second["id"], {"mm_voice": "voice-b"})
        self.assertEqual((await self.store.preferences(first["id"]))["mm_voice"], "voice-a")
        self.assertEqual((await self.store.preferences(second["id"]))["mm_voice"], "voice-b")

    async def test_passwords_are_hashed_and_bad_password_is_rejected(self):
        account = await self.store.register("person@example.com", "long-password", "Person")
        self.assertNotIn("long-password", account["password_hash"])
        self.assertIsNotNone(await self.store.authenticate("person@example.com", "long-password"))
        self.assertIsNone(await self.store.authenticate("person@example.com", "wrong-password"))

    async def test_reports_are_private_to_their_owner(self):
        first = await self.store.register("a@example.com", "password-a", "A")
        second = await self.store.register("b@example.com", "password-b", "B")
        await self.store.save_report(first["id"], "First report", "private transcript", 8)
        first_reports = await self.store.reports(first["id"])
        self.assertEqual(len(first_reports), 1)
        self.assertEqual(await self.store.reports(second["id"]), [])
        self.assertNotIn("transcript", first_reports[0])

    async def test_daily_quota_stops_at_limit(self):
        account = await self.store.register("quota@example.com", "password-q", "Quota")
        with patch.object(commercial, "DAILY_REQUEST_LIMIT", 2):
            self.assertEqual(await self.store.consume(account["id"]), (True, 1))
            self.assertEqual(await self.store.consume(account["id"]), (True, 2))
            self.assertEqual(await self.store.consume(account["id"]), (False, 2))


class CommercialCredentialIsolationTests(unittest.TestCase):
    class Request:
        headers = {"Authorization": "Bearer customer-supplied", "X-MM-Group": "customer-group"}
        query = {"appid": "customer-app", "apikey": "customer-key"}

    def test_commercial_mode_ignores_customer_provider_credentials(self):
        with patch.object(server, "COMMERCIAL_MODE", True), \
             patch.object(server, "ENV_KEY", "server-chat"), \
             patch.object(server, "ENV_GROUP", "server-group"), \
             patch.object(server, "ENV_XF_APPID", "server-app"), \
             patch.object(server, "ENV_XF_APIKEY", "server-xf"):
            self.assertEqual(server.key_from(self.Request()), "server-chat")
            self.assertEqual(server.group_from(self.Request()), "server-group")
            self.assertEqual(server.xf_credentials(self.Request()), ("server-app", "server-xf"))

    def test_commercial_provider_settings_never_read_local_backup(self):
        with patch.object(server, "COMMERCIAL_MODE", True), \
             patch.object(server, "ENV_KEY", "server-chat"), \
             patch.object(server, "ENV_DEEPSEEK_KEY", "server-deepseek"), \
             patch.object(server, "_read_local_settings", side_effect=AssertionError("must not read local settings")):
            self.assertEqual(server._provider_settings()["mm_key"], "server-chat")
            self.assertEqual(server._provider_settings()["ds_key"], "server-deepseek")

    def test_websocket_tickets_are_path_bound_and_single_use(self):
        commercial.WS_TICKETS.clear()
        ticket = commercial._issue_ws_ticket("user-1", "/ws/tts")
        self.assertIsNone(commercial._consume_ws_ticket(ticket, "/ws/asr"))
        self.assertIsNone(commercial._consume_ws_ticket(ticket, "/ws/tts"))

        ticket = commercial._issue_ws_ticket("user-1", "/ws/tts")
        self.assertEqual(commercial._consume_ws_ticket(ticket, "/ws/tts"), "user-1")
        self.assertIsNone(commercial._consume_ws_ticket(ticket, "/ws/tts"))


if __name__ == "__main__":
    unittest.main()
