from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]
BOARD = ROOT / "main" / "boards" / "nologo" / "xingzhi-cube-1.54tft-wifi"


class XingzhiCodexCompletionSummaryTest(unittest.TestCase):
    def test_receiver_parses_summary_packet(self):
        source = (BOARD / "codex_status_receiver.cc").read_text(encoding="utf-8")
        self.assertIn('XINGZHI_CODEX_V1 SUMMARY %d%n', source)
        self.assertIn("summary_callback_(daily_tokens)", source)

    def test_board_displays_completion_and_daily_tokens(self):
        source = (BOARD / "xingzhi-cube-1.54tft-wifi.cc").read_text(encoding="utf-8")
        self.assertIn("AI已被严肃鞭策", source)
        self.assertIn("今日 Token: ", source)
        self.assertIn("ShowNotification(message, 8000)", source)


if __name__ == "__main__":
    unittest.main()
