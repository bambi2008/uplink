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


class MobileSettingsIsolationTests(unittest.TestCase):
    def test_managed_client_never_receives_credential_values(self):
        payload = server._settings_for_client({
            "mm_key": "chat-secret",
            "xf_apikey": "speech-secret",
            "db_token": "doubao-secret",
            "user_name": "Mao",
            "mm_pace": "normal",
        }, managed_credentials=True)

        self.assertEqual(payload["settings"], {"user_name": "Mao", "mm_pace": "normal"})
        self.assertTrue(payload["managed_credentials"])
        self.assertTrue(payload["configured"]["mm_key"])
        self.assertNotIn("chat-secret", repr(payload))
        self.assertNotIn("speech-secret", repr(payload))
        self.assertNotIn("doubao-secret", repr(payload))

    def test_mobile_preference_save_preserves_all_credentials(self):
        current = {
            "mm_key": "chat-secret",
            "mm_tts_key": "voice-secret",
            "xf_apikey": "speech-secret",
            "db_token": "doubao-secret",
            "user_name": "Mao",
            "mm_pace": "normal",
        }
        incoming = {
            "mm_key": "replacement-must-be-ignored",
            "xf_apikey": "replacement-must-be-ignored",
            "db_token": "replacement-must-be-ignored",
            "user_name": "Jose",
            "mm_pace": "calm",
        }

        merged = server._merge_settings(current, incoming, allow_credentials=False)

        self.assertEqual(merged["mm_key"], "chat-secret")
        self.assertEqual(merged["mm_tts_key"], "voice-secret")
        self.assertEqual(merged["xf_apikey"], "speech-secret")
        self.assertEqual(merged["db_token"], "doubao-secret")
        self.assertEqual(merged["user_name"], "Jose")
        self.assertEqual(merged["mm_pace"], "calm")

    def test_pairing_token_comparison_is_exact(self):
        with patch.object(server, "MOBILE_ACCESS_TOKEN", "phone-token"):
            self.assertTrue(server._mobile_token_valid("phone-token"))
            self.assertFalse(server._mobile_token_valid("phone-token "))
            self.assertFalse(server._mobile_token_valid("wrong"))

    def test_forwarded_request_is_remote(self):
        request = FakeRequest({}, {"CF-Connecting-IP": "203.0.113.8"})
        request.remote = "127.0.0.1"
        self.assertTrue(server._is_remote_request(request))

    def test_tailscale_serve_request_is_remote(self):
        request = FakeRequest({}, {"Tailscale-User-Login": "owner@example.com"})
        request.remote = "127.0.0.1"
        self.assertTrue(server._is_remote_request(request))
        self.assertTrue(server._request_is_https(request))

    def test_mobile_mode_never_opens_remote_access_without_a_token(self):
        request = FakeRequest({}, {"CF-Connecting-IP": "203.0.113.8"})
        request.remote = "127.0.0.1"
        with patch.object(server, "MOBILE_MODE", True), patch.object(server, "MOBILE_ACCESS_TOKEN", ""):
            self.assertTrue(server._mobile_access_required(request))
            self.assertFalse(server._mobile_request_authorized(request))


class LatencyP0Tests(unittest.TestCase):
    def setUp(self):
        server.MODEL_NEGATIVE_CACHE.clear()

    def test_only_explicit_model_unavailable_errors_are_negative_cached(self):
        self.assertTrue(server._model_failure_is_cacheable(404, "model MiniMax-X not found"))
        self.assertTrue(server._model_failure_is_cacheable(400, "unsupported model"))
        for status in (401, 403, 429, 500, 503):
            self.assertFalse(server._model_failure_is_cacheable(status, "model not available"))
        self.assertFalse(server._model_failure_is_cacheable(400, "invalid request body"))

    def test_negative_model_cache_expires(self):
        with patch.object(server, "CHAT_MODELS", ("primary", "fallback")):
            server._cache_model_failure("primary", now=100.0)
            self.assertEqual(server._chat_model_candidates(now=101.0), ["fallback"])
            self.assertEqual(server._chat_model_candidates(now=701.0), ["primary", "fallback"])

    def test_latency_sanitizer_drops_credentials_transcripts_and_audio(self):
        clean = server._sanitize_latency({
            "turn_id": "anonymous-1", "duration_ms": 123.4,
            "semantic_first_audio_ms": 876.5,
            "Authorization": "Bearer secret", "api_key": "secret",
            "AccessToken": "secret", "transcript": "full words",
            "prompt_text": "private words", "audio_bytes_raw": "private audio",
        })
        self.assertEqual(clean, {"turn_id": "anonymous-1", "duration_ms": 123.4,
                                 "semantic_first_audio_ms": 876.5})


class LatencyTraceDisabledTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_trace_does_not_create_latency_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "latency.jsonl"
            with patch.object(server, "LATENCY_TRACE", False), patch.object(server, "LATENCY_FILE", path):
                await server.record_latency({"type": "turn_latency", "duration_ms": 123})
            self.assertFalse(path.exists())


class SharedHttpClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_application_client_is_created_once_and_closed_on_cleanup(self):
        app = {}
        context = server.http_client_context(app)
        await anext(context)
        client = app[server.HTTP_CLIENT_KEY]
        self.assertFalse(client.closed)
        with self.assertRaises(StopAsyncIteration):
            await anext(context)
        self.assertTrue(client.closed)


class StreamingTtsProtocolTests(unittest.TestCase):
    def test_connected_and_started_events_are_recognized(self):
        self.assertTrue(server._upstream_event_ok({
            "event": "connected_success", "base_resp": {"status_code": 0}
        }, "connected_success"))
        self.assertTrue(server._upstream_event_ok({
            "event": "task_started", "base_resp": {"status_code": 0}
        }, "task_started"))
        self.assertFalse(server._upstream_event_ok({
            "event": "task_failed", "base_resp": {"status_code": 1004}
        }, "task_started"))

    def test_task_start_matches_official_websocket_schema(self):
        event = server._tts_start_event({
            "voice": "English_magnetic_voiced_man", "model": "speech-2.8-turbo",
            "language_boost": "English", "emotion": "happy",
        })
        self.assertEqual(event["event"], "task_start")
        self.assertEqual(event["model"], "speech-2.8-turbo")
        self.assertEqual(event["voice_setting"]["voice_id"], "English_magnetic_voiced_man")
        self.assertEqual(event["audio_setting"], {
            "sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1,
        })

    def test_multiple_segments_are_strictly_ordered(self):
        relay = server.TTSRelayState()
        relay.begin("reply-1")
        self.assertEqual(relay.queue_segment(0), 0)
        self.assertEqual(relay.queue_segment(1), 1)
        with self.assertRaises(ValueError):
            relay.queue_segment(1)

    def test_segment_order_remains_strict_after_completed_segment_is_removed(self):
        relay = server.TTSRelayState()
        relay.begin("reply-1")
        relay.queue_segment(0)
        relay.accept_upstream({"is_final": True})
        with self.assertRaises(ValueError):
            relay.queue_segment(0)

    def test_connection_cannot_be_reused_for_a_second_reply(self):
        relay = server.TTSRelayState()
        relay.begin("reply-1")
        relay.finished = True
        with self.assertRaises(ValueError):
            relay.begin("reply-2")

    def test_hex_audio_and_final_are_forwarded_in_order(self):
        relay = server.TTSRelayState()
        relay.begin("reply-1")
        relay.queue_segment(0)
        actions = relay.accept_upstream({"data": {"audio": "494433"}, "is_final": True})
        self.assertEqual(actions, [
            ("binary", b"ID3"),
            ("json", {"type": "segment_finished", "segment_id": 0}),
        ])
        finished = relay.accept_upstream({"event": "task_finished"})
        self.assertEqual(finished, [("json", {"type": "reply_finished", "reply_id": "reply-1"})])

    def test_cancel_stops_forwarding(self):
        relay = server.TTSRelayState()
        relay.begin("reply-1")
        relay.queue_segment(0)
        relay.cancel()
        self.assertEqual(relay.accept_upstream({"data": {"audio": "494433"}, "is_final": True}), [])

    def test_invalid_audio_and_secret_errors_are_safe(self):
        with self.assertRaisesRegex(ValueError, "invalid audio"):
            server._decode_tts_audio({"data": {"audio": "not-hex"}})
        secret = "private-secret-value"
        safe = server._safe_tts_error("Authorization Bearer " + secret, secret)
        self.assertNotIn(secret, safe)
        self.assertNotIn("Bearer", safe)

    def test_continue_event_uses_official_fields(self):
        self.assertEqual(server._tts_continue_event("hello"), {
            "event": "task_continue", "text": "hello",
        })


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
