from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]
APPLICATION_HEADER = ROOT / "main/application.h"
APPLICATION_SOURCE = ROOT / "main/application.cc"
BOARD_SOURCE = ROOT / "main/boards/nologo/xingzhi-cube-1.54tft-wifi/xingzhi-cube-1.54tft-wifi.cc"


class XingzhiCodexListeningMediaTest(unittest.TestCase):
    def test_listening_pauses_and_non_listening_conditionally_resumes_codex_media(self):
        header = APPLICATION_HEADER.read_text(encoding="utf-8")
        application = APPLICATION_SOURCE.read_text(encoding="utf-8")
        board = BOARD_SOURCE.read_text(encoding="utf-8")

        self.assertIn("RegisterDeviceStateCallback", header)
        self.assertIn("device_state_callback_(new_state)", application)
        self.assertIn("RegisterDeviceStateCallback", board)
        self.assertIn("new_state == kDeviceStateListening", board)
        self.assertIn("codex_running_.load()", board)
        self.assertIn("codex_media_enabled_", board)
        self.assertIn("while (codex_running_.load() && codex_media_enabled_.load())", board)


if __name__ == "__main__":
    unittest.main()
