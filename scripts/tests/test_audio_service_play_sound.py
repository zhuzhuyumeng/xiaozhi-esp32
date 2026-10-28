import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
AUDIO_SERVICE = ROOT / "main" / "audio" / "audio_service.cc"


class AudioServicePlaySoundTest(unittest.TestCase):
    def test_play_sound_binds_all_packets_to_one_generation(self):
        source = AUDIO_SERVICE.read_text(encoding="utf-8")
        match = re.search(
            r"void AudioService::PlaySound\(.*?\n}\n\n", source, re.DOTALL
        )
        self.assertIsNotNone(match)
        play_sound = match.group(0)

        capture = "const uint32_t generation = GetPlaybackGeneration();"
        generation_enqueue = (
            "PushPacketToDecodeQueueForGeneration(std::move(packet), true, generation)"
        )
        self.assertIn(capture, play_sound)
        self.assertIn(generation_enqueue, play_sound)
        self.assertLess(play_sound.index(capture), play_sound.index("demuxer->OnPacket"))


if __name__ == "__main__":
    unittest.main()
