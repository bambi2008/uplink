import json
import pathlib
import tempfile
import unittest

import mobile_launcher


class MobileLauncherTests(unittest.TestCase):
    def test_access_token_is_persistent_and_not_short(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "mobile-access.json"
            first = mobile_launcher.load_or_create_token(path)
            second = mobile_launcher.load_or_create_token(path)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(first, second)
        self.assertEqual(payload["access_token"], first)
        self.assertGreaterEqual(len(first), 32)

    def test_quick_tunnel_url_parser_accepts_cloudflare_url(self):
        line = "INF Your quick Tunnel has been created! Visit it at https://quiet-sun.trycloudflare.com"
        match = mobile_launcher.PUBLIC_URL_RE.search(line)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(0), "https://quiet-sun.trycloudflare.com")

    def test_tailscale_url_parser_accepts_stable_tailnet_url(self):
        line = "Available within your tailnet: https://home-pc.example-tailnet.ts.net"
        match = mobile_launcher.TAILSCALE_URL_RE.search(line)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(0), "https://home-pc.example-tailnet.ts.net")


if __name__ == "__main__":
    unittest.main()
