"""Bridge Codex lifecycle hooks to the Xingzhi display over UDP."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from datetime import date
from typing import Any, Iterator


PROTOCOL_PREFIX = "XINGZHI_CODEX_V1"
ACK_PREFIX = "XINGZHI_CODEX_ACK_V1"
DEFAULT_PORT = 42154
DEFAULT_HOST = "255.255.255.255"
VOICE_PREFIX = "XINGZHI_VOICE_V1 "
VOICE_ACK_PREFIX = "XINGZHI_VOICE_ACK_V1"
DEFAULT_VOICE_PORT = 42155
MAX_VOICE_TEXT_BYTES = 1024
SUMMARY_PREFIX = "XINGZHI_CODEX_V1 SUMMARY"
FAILURE_PREFIX = "XINGZHI_CODEX_V1 FAILURE"
STATE_SCHEMA_VERSION = 2


def default_state_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    return base / "xingzhi-ai" / "codex-status.json"


@contextlib.contextmanager
def locked_state(path: Path) -> Iterator[dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        _lock_file(lock_file)
        try:
            state: dict[str, Any] = {
                "version": STATE_SCHEMA_VERSION,
                "active_turns": {},
                "current_thread": None,
            }
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if loaded.get("version") == 1:
                    loaded["version"] = STATE_SCHEMA_VERSION
                    loaded["current_thread"] = None
                if loaded.get("version") != STATE_SCHEMA_VERSION:
                    raise ValueError(f"unsupported state schema: {loaded.get('version')!r}")
                if not isinstance(loaded.get("active_turns"), dict):
                    raise ValueError("active_turns must be an object")
                state = loaded
            yield state
            temp_path = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
            temp_path.write_text(
                json.dumps(state, ensure_ascii=True, sort_keys=True), encoding="utf-8"
            )
            os.replace(temp_path, path)
        finally:
            _unlock_file(lock_file)


def _lock_file(file_object: Any) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(file_object.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(file_object.fileno(), fcntl.LOCK_EX)


def _unlock_file(file_object: Any) -> None:
    file_object.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(file_object.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(file_object.fileno(), fcntl.LOCK_UN)


def apply_hook_event(state: dict[str, Any], event: dict[str, Any]) -> tuple[str, int]:
    event_name = _required_string(event, "hook_event_name")
    session_id = _required_string(event, "session_id")
    active_turns = state["active_turns"]
    state.setdefault("current_thread", None)

    if event_name in {"SessionStart", "UserPromptSubmit"}:
        state["current_thread"] = {
            "id": session_id,
            "cwd": _required_path_string(event, "cwd"),
            "updated_at": int(time.time()),
        }

    if event_name == "SessionStart":
        # Media follows the conversation currently shown in ChatGPT.  A
        # previous process may have exited without emitting Stop, so discard
        # stale turns when a new conversation becomes current.
        state["active_turns"] = {}
    elif event_name == "UserPromptSubmit":
        turn_id = _required_string(event, "turn_id")
        # A Codex session cannot run two main-thread turns at once. A new
        # prompt is the authoritative start of the current conversation turn.
        state["active_turns"] = {}
        active_turns = state["active_turns"]
        active_turns[turn_id] = {"session_id": session_id, "started_at": int(time.time())}
    elif event_name in {"Stop", "Interrupt"}:
        turn_id = _required_string(event, "turn_id")
        current_thread = state.get("current_thread")
        if isinstance(current_thread, dict) and current_thread.get("id") == session_id:
            active_turns.pop(turn_id, None)
    elif event_name == "SessionEnd":
        current_thread = state.get("current_thread")
        if isinstance(current_thread, dict) and current_thread.get("id") == session_id:
            state["active_turns"] = {}
        if isinstance(current_thread, dict) and current_thread.get("id") == session_id:
            state["current_thread"] = None
    else:
        raise ValueError(f"unsupported hook event: {event_name}")

    count = len(state["active_turns"])
    return ("RUNNING" if count else "IDLE", count)


def _required_string(value: dict[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result or len(result) > 128:
        raise ValueError(f"{field} must be a non-empty string of at most 128 characters")
    return result


def _required_path_string(value: dict[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result or len(result) > 4096:
        raise ValueError(f"{field} must be a non-empty string of at most 4096 characters")
    return result


def build_packet(status: str, active_count: int) -> bytes:
    if status not in {"RUNNING", "IDLE"}:
        raise ValueError(f"unsupported status: {status}")
    if active_count < 0 or active_count > 999:
        raise ValueError("active_count must be between 0 and 999")
    if (status == "RUNNING") != (active_count > 0):
        raise ValueError("status and active_count disagree")
    return f"{PROTOCOL_PREFIX} {status} {active_count}".encode("ascii")


def build_summary_packet(daily_tokens: int) -> bytes:
    if daily_tokens < 0 or daily_tokens > 2_147_483_647:
        raise ValueError("daily_tokens must be between 0 and 2147483647")
    return f"{SUMMARY_PREFIX} {daily_tokens}".encode("ascii")


def build_failure_packet() -> bytes:
    return FAILURE_PREFIX.encode("ascii")


def is_failed_event(
    event: dict[str, Any], sessions_root: Path | None = None
) -> bool:
    if event.get("hook_event_name") == "Interrupt":
        return True
    if event.get("hook_event_name") != "Stop":
        return False
    if any(
        isinstance(event.get(field), str) and event[field].strip()
        for field in ("error", "error_message", "stop_reason", "failure_reason")
    ):
        return True
    session_id = event.get("session_id")
    turn_id = event.get("turn_id")
    if not isinstance(session_id, str) or not isinstance(turn_id, str):
        return False
    root = sessions_root or (Path.home() / ".codex" / "sessions")
    for session_path in root.glob(f"**/*{session_id}*.jsonl"):
        with session_path.open("r", encoding="utf-8") as session_file:
            for line in session_file:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict) or payload.get("turn_id") != turn_id:
                    continue
                if payload.get("type") in {"turn_aborted", "turn_failed", "stream_error"}:
                    return True
    return False


def daily_token_count(sessions_root: Path | None = None, today: date | None = None) -> int:
    root = sessions_root or (Path.home() / ".codex" / "sessions")
    current_date = today or date.today()
    day_dir = root / f"{current_date:%Y}" / f"{current_date:%m}" / f"{current_date:%d}"
    total = 0
    for session_path in sorted(day_dir.glob("*.jsonl")):
        latest_total = None
        with session_path.open("r", encoding="utf-8") as session_file:
            for line in session_file:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                info = payload.get("info")
                usage = info.get("total_token_usage") if isinstance(info, dict) else None
                value = usage.get("total_tokens") if isinstance(usage, dict) else None
                if isinstance(value, int) and value >= 0:
                    latest_total = value
        if latest_total is not None:
            total += latest_total
    return total


def send_packet(packet: bytes, host: str, port: int, attempts: int = 3) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for attempt in range(attempts):
            udp_socket.sendto(packet, (host, port))
            if attempt + 1 < attempts:
                time.sleep(0.1)


def probe_device(host: str, port: int, timeout: float, status: str = "RUNNING") -> str:
    active_count = 1 if status == "RUNNING" else 0
    packet = build_packet(status, active_count)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_socket.bind(("0.0.0.0", 0))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no device ACK within {timeout:.1f} seconds")
            udp_socket.sendto(packet, (host, port))
            udp_socket.settimeout(min(0.5, remaining))
            try:
                response, _ = udp_socket.recvfrom(128)
                break
            except socket.timeout:
                continue
    text = response.decode("ascii", errors="strict")
    if text != f"{ACK_PREFIX} {status} {active_count}":
        raise RuntimeError(f"unexpected device ACK: {text!r}")
    return text


def build_voice_packet(text: str) -> bytes:
    normalized = text.strip()
    if not normalized or "\x00" in normalized:
        raise ValueError("voice text must not be empty or contain NUL")
    encoded = normalized.encode("utf-8", errors="strict")
    if len(encoded) > MAX_VOICE_TEXT_BYTES:
        raise ValueError(f"voice text exceeds {MAX_VOICE_TEXT_BYTES} UTF-8 bytes")
    return VOICE_PREFIX.encode("ascii") + encoded


def parse_voice_packet(packet: bytes) -> str:
    prefix = VOICE_PREFIX.encode("ascii")
    if not packet.startswith(prefix):
        raise ValueError("unsupported voice packet prefix")
    if len(packet) > len(prefix) + MAX_VOICE_TEXT_BYTES:
        raise ValueError("voice packet is too large")
    return build_voice_packet(packet[len(prefix) :].decode("utf-8", errors="strict"))[
        len(prefix) :
    ].decode("utf-8")


def current_thread(state_file: Path) -> tuple[str, str]:
    with locked_state(state_file) as state:
        selected = state.get("current_thread")
        if not isinstance(selected, dict):
            raise RuntimeError("no current Codex thread is available")
        return (
            _required_string(selected, "id"),
            _required_path_string(selected, "cwd"),
        )


def run_voice_listener(args: argparse.Namespace) -> int:
    from codex_app_server_client import deliver_voice_turn

    logging.basicConfig(
        filename=args.log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )
    expected_device_ip = socket.gethostbyname(args.device_host)
    delivery_lock = threading.Lock()

    def deliver(text: str, thread_id: str, cwd: str) -> None:
        with delivery_lock:
            try:
                logging.info("Delivering voice text to thread %s: %s", thread_id, text)
                turn_id = deliver_voice_turn(
                    [
                        args.codex_executable,
                        "app-server",
                        "-c",
                        f'model_provider="{args.model_provider}"',
                        "-c",
                        f'model="{args.model}"',
                    ],
                    thread_id,
                    cwd,
                    text,
                )
                logging.info("Voice turn completed: %s", turn_id)
            except Exception:
                logging.exception("Voice delivery failed")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind((args.listen_host, args.voice_port))
        logging.info(
            "Listening on UDP/%d for Xingzhi voice packets from %s",
            args.voice_port,
            expected_device_ip,
        )
        while True:
            packet, sender = listener.recvfrom(len(VOICE_PREFIX) + MAX_VOICE_TEXT_BYTES + 1)
            if sender[0] != expected_device_ip:
                logging.warning("Rejected voice packet from unexpected host %s", sender[0])
                continue
            try:
                text = parse_voice_packet(packet)
                thread_id, cwd = current_thread(args.state_file)
            except Exception as exc:
                logging.error("Rejected voice packet: %s", exc)
                listener.sendto(f"{VOICE_ACK_PREFIX} REJECTED".encode("ascii"), sender)
                continue
            listener.sendto(f"{VOICE_ACK_PREFIX} ACCEPTED".encode("ascii"), sender)
            threading.Thread(
                target=deliver, args=(text, thread_id, cwd), daemon=True
            ).start()


def probe_voice_listener(host: str, port: int, text: str, timeout: float) -> str:
    packet = build_voice_packet(text)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.settimeout(timeout)
        udp_socket.sendto(packet, (host, port))
        response, _ = udp_socket.recvfrom(128)
    ack = response.decode("ascii", errors="strict")
    if ack != f"{VOICE_ACK_PREFIX} ACCEPTED":
        raise RuntimeError(f"voice listener rejected packet: {ack!r}")
    return ack


def run_hook(args: argparse.Namespace) -> int:
    event = json.load(sys.stdin)
    if not isinstance(event, dict):
        raise ValueError("hook input must be a JSON object")
    with locked_state(args.state_file) as state:
        status, active_count = apply_hook_event(state, event)
    send_packet(build_packet(status, active_count), args.host, args.port)
    if status == "IDLE" and is_failed_event(event):
        send_packet(build_failure_packet(), args.host, args.port)
    elif status == "IDLE" and event["hook_event_name"] in {"Stop", "SessionEnd"}:
        send_packet(build_summary_packet(daily_token_count()), args.host, args.port)
    print("{}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("XINGZHI_DEVICE_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--state-file", type=Path, default=default_state_path())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("hook", help="consume one Codex hook event from stdin")
    send_parser = subparsers.add_parser("send", help="send a state without modifying hook state")
    send_parser.add_argument("status", choices=("running", "idle"))
    probe_parser = subparsers.add_parser("probe", help="require an ACK from a real device")
    probe_parser.add_argument("--timeout", type=float, default=5.0)
    probe_parser.add_argument("--status", choices=("running", "idle"), default="running")
    voice_listener = subparsers.add_parser(
        "voice-listen", help="relay validated device STT packets to the current Codex thread"
    )
    voice_listener.add_argument("--listen-host", default="0.0.0.0")
    voice_listener.add_argument("--device-host", required=True)
    voice_listener.add_argument("--voice-port", type=int, default=DEFAULT_VOICE_PORT)
    voice_listener.add_argument("--codex-executable", default="codex")
    voice_listener.add_argument(
        "--model-provider", default=os.environ.get("CODEX_MODEL_PROVIDER", "custom")
    )
    voice_listener.add_argument(
        "--model", default=os.environ.get("CODEX_MODEL", "gpt-5.6-sol")
    )
    voice_listener.add_argument(
        "--log-file", type=Path, default=default_state_path().with_name("voice-relay.log")
    )
    voice_probe = subparsers.add_parser(
        "voice-probe", help="send a text packet to a running voice listener"
    )
    voice_probe.add_argument("text")
    voice_probe.add_argument("--voice-port", type=int, default=DEFAULT_VOICE_PORT)
    voice_probe.add_argument("--timeout", type=float, default=3.0)
    subparsers.add_parser("status", help="print the persisted active-turn state")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "hook":
        return run_hook(args)
    if args.command == "send":
        active_count = 1 if args.status == "running" else 0
        send_packet(build_packet(args.status.upper(), active_count), args.host, args.port)
        return 0
    if args.command == "probe":
        print(probe_device(args.host, args.port, args.timeout, args.status.upper()))
        return 0
    if args.command == "voice-listen":
        return run_voice_listener(args)
    if args.command == "voice-probe":
        print(probe_voice_listener(args.host, args.voice_port, args.text, args.timeout))
        return 0
    with locked_state(args.state_file) as state:
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"codex-status-bridge: {exc}", file=sys.stderr)
        raise SystemExit(1)
