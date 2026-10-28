import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from datetime import date


MODULE_PATH = Path(__file__).parents[1] / "codex_status_bridge.py"
SPEC = importlib.util.spec_from_file_location("codex_status_bridge", MODULE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bridge)


class CodexStatusBridgeTest(unittest.TestCase):
    def test_current_conversation_controls_running_state(self):
        state = {"version": 2, "active_turns": {}, "current_thread": None}
        self.assertEqual(
            bridge.apply_hook_event(state, self.event("UserPromptSubmit", "turn-1")),
            ("RUNNING", 1),
        )
        self.assertEqual(
            bridge.apply_hook_event(
                state, self.event("UserPromptSubmit", "turn-2", "session-2")
            ),
            ("RUNNING", 1),
        )
        self.assertEqual(
            bridge.apply_hook_event(state, self.event("Stop", "turn-1")),
            ("RUNNING", 1),
        )
        self.assertEqual(
            bridge.apply_hook_event(
                state, self.event("Interrupt", "turn-2", "session-2")
            ),
            ("IDLE", 0),
        )

    def test_session_start_clears_stale_turns_from_previous_process(self):
        state = {
            "version": 2,
            "active_turns": {"stale": {"session_id": "old-session", "started_at": 1}},
            "current_thread": {"id": "old-session", "cwd": "C:\\work", "updated_at": 1},
        }
        result = bridge.apply_hook_event(
            state,
            {
                "hook_event_name": "SessionStart",
                "session_id": "new-session",
                "cwd": "F:\\codex\\esp32",
            },
        )
        self.assertEqual(result, ("IDLE", 0))
        self.assertEqual(state["active_turns"], {})
        self.assertEqual(state["current_thread"]["id"], "new-session")

    def test_session_end_removes_current_session(self):
        state = {
            "version": 2,
            "active_turns": {
                "a": {"session_id": "session-a", "started_at": 1},
                "b": {"session_id": "session-b", "started_at": 1},
            },
            "current_thread": {
                "id": "session-a",
                "cwd": "C:\\work",
                "updated_at": 1,
            },
        }
        result = bridge.apply_hook_event(
            state, {"hook_event_name": "SessionEnd", "session_id": "session-a"}
        )
        self.assertEqual(result, ("IDLE", 0))
        self.assertEqual(state["active_turns"], {})
        self.assertIsNone(state["current_thread"])

    def test_old_session_end_does_not_stop_new_current_turn(self):
        state = {"version": 2, "active_turns": {}, "current_thread": None}
        bridge.apply_hook_event(state, self.event("UserPromptSubmit", "new-turn", "new"))
        result = bridge.apply_hook_event(
            state, {"hook_event_name": "SessionEnd", "session_id": "old"}
        )
        self.assertEqual(result, ("RUNNING", 1))
        self.assertIn("new-turn", state["active_turns"])
        self.assertEqual(state["current_thread"]["id"], "new")

    def test_new_turn_replaces_all_stale_turns(self):
        state = {
            "version": 2,
            "active_turns": {
                "stale": {"session_id": "session", "started_at": 1},
                "other": {"session_id": "other-session", "started_at": 1},
            },
            "current_thread": None,
        }
        result = bridge.apply_hook_event(
            state, self.event("UserPromptSubmit", "replacement")
        )
        self.assertEqual(result, ("RUNNING", 1))
        self.assertEqual(set(state["active_turns"]), {"replacement"})

    def test_session_start_selects_current_thread_without_starting_media(self):
        state = {"version": 2, "active_turns": {}, "current_thread": None}
        result = bridge.apply_hook_event(
            state,
            {
                "hook_event_name": "SessionStart",
                "session_id": "desktop-thread",
                "cwd": "F:\\codex\\esp32",
            },
        )
        self.assertEqual(result, ("IDLE", 0))
        self.assertEqual(state["current_thread"]["id"], "desktop-thread")
        self.assertEqual(state["current_thread"]["cwd"], "F:\\codex\\esp32")

    def test_version_one_state_migrates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"version": 1, "active_turns": {}}), encoding="utf-8"
            )
            with bridge.locked_state(path) as state:
                self.assertEqual(state["version"], 2)
                self.assertIsNone(state["current_thread"])

    def test_packet_validation(self):
        self.assertEqual(
            bridge.build_packet("RUNNING", 2), b"XINGZHI_CODEX_V1 RUNNING 2"
        )
        with self.assertRaises(ValueError):
            bridge.build_packet("IDLE", 1)

    def test_summary_packet_and_daily_token_count(self):
        self.assertEqual(
            bridge.build_summary_packet(1234),
            b"XINGZHI_CODEX_V1 SUMMARY 1234",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = root / "2026" / "09" / "28"
            day.mkdir(parents=True)
            (day / "one.jsonl").write_text(
                '\n'.join([
                    json.dumps({"payload": {"type": "token_count", "info": {
                        "total_token_usage": {"total_tokens": 10}}}}),
                    json.dumps({"payload": {"type": "token_count", "info": {
                        "total_token_usage": {"total_tokens": 25}}}}),
                ]),
                encoding="utf-8",
            )
            (day / "two.jsonl").write_text(
                json.dumps({"payload": {"type": "token_count", "info": {
                    "total_token_usage": {"total_tokens": 7}}}}),
                encoding="utf-8",
            )
            self.assertEqual(
                bridge.daily_token_count(root, date(2026, 9, 28)),
                32,
            )

    def test_interrupt_is_failure_but_normal_stop_is_completion(self):
        self.assertEqual(bridge.build_failure_packet(), b"XINGZHI_CODEX_V1 FAILURE")
        self.assertTrue(bridge.is_failed_event(self.event("Interrupt", "turn-1")))
        self.assertFalse(bridge.is_failed_event(self.event("Stop", "turn-1")))
        failed = self.event("Stop", "turn-1")
        failed["error_message"] = "network disconnected"
        self.assertTrue(bridge.is_failed_event(failed))

    def test_stop_detects_aborted_turn_from_session_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "rollout-session.jsonl"
            session.write_text(
                json.dumps({
                    "type": "event_msg",
                    "payload": {
                        "type": "turn_aborted",
                        "turn_id": "turn-1",
                        "reason": "network error",
                    },
                }),
                encoding="utf-8",
            )
            self.assertTrue(
                bridge.is_failed_event(self.event("Stop", "turn-1"), root)
            )

    def test_voice_packet_round_trip_and_limits(self):
        packet = bridge.build_voice_packet("  帮我运行测试  ")
        self.assertEqual(packet, "XINGZHI_VOICE_V1 帮我运行测试".encode("utf-8"))
        self.assertEqual(bridge.parse_voice_packet(packet), "帮我运行测试")
        with self.assertRaises(ValueError):
            bridge.parse_voice_packet(b"UNKNOWN hello")
        with self.assertRaises(ValueError):
            bridge.build_voice_packet("x" * (bridge.MAX_VOICE_TEXT_BYTES + 1))

    def test_current_thread_reads_exact_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "active_turns": {},
                        "current_thread": {
                            "id": "thread-id",
                            "cwd": "F:\\codex\\esp32",
                            "updated_at": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                bridge.current_thread(path), ("thread-id", "F:\\codex\\esp32")
            )

    def test_locked_state_persists_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with bridge.locked_state(path) as state:
                state["active_turns"]["turn"] = {"session_id": "session", "started_at": 1}
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("turn", loaded["active_turns"])

    def test_udp_sender_reaches_loopback_listener(self):
        received = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.settimeout(2)

            def receive_one():
                received.append(listener.recvfrom(128)[0])

            thread = threading.Thread(target=receive_one)
            thread.start()
            bridge.send_packet(bridge.build_packet("RUNNING", 1), "127.0.0.1", port, 1)
            thread.join(timeout=2)
        self.assertEqual(received, [b"XINGZHI_CODEX_V1 RUNNING 1"])

    def test_probe_retries_until_ack_arrives(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as device:
            device.bind(("127.0.0.1", 0))
            port = device.getsockname()[1]
            received_count = []

            def acknowledge_second_packet():
                while len(received_count) < 2:
                    packet, sender = device.recvfrom(128)
                    received_count.append(packet)
                device.sendto(b"XINGZHI_CODEX_ACK_V1 RUNNING 1", sender)

            thread = threading.Thread(target=acknowledge_second_packet)
            thread.start()
            response = bridge.probe_device("127.0.0.1", port, 2, "RUNNING")
            thread.join(timeout=2)
        self.assertEqual(response, "XINGZHI_CODEX_ACK_V1 RUNNING 1")
        self.assertEqual(len(received_count), 2)

    def test_idle_probe_requires_exact_idle_ack(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as device:
            device.bind(("127.0.0.1", 0))
            port = device.getsockname()[1]

            def acknowledge_idle():
                packet, sender = device.recvfrom(128)
                self.assertEqual(packet, b"XINGZHI_CODEX_V1 IDLE 0")
                device.sendto(b"XINGZHI_CODEX_ACK_V1 IDLE 0", sender)

            thread = threading.Thread(target=acknowledge_idle)
            thread.start()
            response = bridge.probe_device("127.0.0.1", port, 2, "IDLE")
            thread.join(timeout=2)
        self.assertEqual(response, "XINGZHI_CODEX_ACK_V1 IDLE 0")

    @staticmethod
    def event(name, turn_id, session_id="session"):
        return {
            "hook_event_name": name,
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": "F:\\codex\\esp32",
        }


if __name__ == "__main__":
    unittest.main()
