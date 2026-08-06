import unittest

import launcher
import server


class LauncherTests(unittest.TestCase):
    def test_launcher_reads_the_server_build(self):
        self.assertEqual(launcher.expected_build(), server.BUILD)

    def test_unidentified_process_is_never_stopped(self):
        self.assertFalse(launcher.stop_outdated_server({"build": "old", "pid": 1234}))


if __name__ == "__main__":
    unittest.main()
