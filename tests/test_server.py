import hashlib
import hmac
import pathlib
import sys
import unittest
from base64 import b64encode
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server


class FakeRequest:
    def __init__(self, query, headers=None):
        self.query = query
        self.headers = headers or {}


class XunfeiAuthTests(unittest.TestCase):
    def test_explicit_credentials_override_stale_backup(self):
        request = FakeRequest({"appid": "current-app", "apikey": "current-key"})
        with patch.object(server, "_read_local_settings", return_value={
            "xf_appid": "stale-app",
            "xf_apikey": "stale-key",
        }):
            self.assertEqual(
                server.xf_credentials(request),
                ("current-app", "current-key"),
            )

    def test_signature_matches_xunfei_documented_formula(self):
        appid = "595f23df"
        apikey = "d9f4aa7ea6d94faca62cd88a28fd5234"
        timestamp = 1512041814
        digest = hashlib.md5(f"{appid}{timestamp}".encode()).hexdigest().encode()
        expected = b64encode(hmac.new(apikey.encode(), digest, hashlib.sha1).digest()).decode()

        with patch.object(server.time, "time", return_value=timestamp):
            query = parse_qs(urlparse(server.xf_handshake_url(appid, apikey)).query)

        self.assertEqual(query["appid"], [appid])
        self.assertEqual(query["ts"], [str(timestamp)])
        self.assertEqual(query["signa"], [expected])

    def test_10110_has_an_actionable_license_message(self):
        detail = server.xf_rejection_detail({"code": "10110", "desc": "no license|illegal signa"})
        self.assertIn("授权不可用", detail)
        self.assertIn("实时语音转写", detail)
        self.assertIn("APIKey", detail)


class MiniMaxCredentialTests(unittest.TestCase):
    def test_explicit_request_key_overrides_stale_backup(self):
        request = FakeRequest({}, {"Authorization": "Bearer current-key"})
        with patch.object(server, "_read_local_settings", return_value={"mm_key": "stale-key"}):
            self.assertEqual(server.key_from(request), "current-key")


class FakeSettingsRequest:
    method = "POST"

    def __init__(self, settings):
        self.settings = settings

    async def json(self):
        return {"settings": self.settings}


class LocalSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_empty_value_removes_optional_credential_and_creates_backup(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            settings_path = pathlib.Path(folder) / "local-settings.json"
            original = {"mm_key": "valid-key", "xf_ise_secret": "s" * 32}
            settings_path.write_text(__import__("json").dumps(original), encoding="utf-8")
            request = FakeSettingsRequest({"xf_ise_key": "", "xf_ise_secret": ""})

            with patch.object(server, "LOCAL_SETTINGS", settings_path):
                response = await server.api_local_settings(request)
                saved = __import__("json").loads(settings_path.read_text(encoding="utf-8"))
                backup = __import__("json").loads(
                    settings_path.with_name("local-settings.backup.json").read_text(encoding="utf-8")
                )

            self.assertEqual(response.status, 200)
            self.assertEqual(saved, {"mm_key": "valid-key"})
            self.assertEqual(backup, original)


if __name__ == "__main__":
    unittest.main()
