"""Minimal JSONL client for delivering text to an existing Codex thread."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from typing import Any, Sequence


class CodexAppServerError(RuntimeError):
    pass


class CodexAppServerClient:
    def __init__(self, command: Sequence[str], timeout: float = 900.0):
        if not command:
            raise ValueError("app-server command must not be empty")
        self._command = tuple(command)
        self._timeout = timeout
        self._next_id = 1
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._stderr_lines: list[str] = []
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        child_environment = os.environ.copy()
        child_environment["PYTHONUTF8"] = "1"
        child_environment["PYTHONIOENCODING"] = "utf-8"
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
            creationflags=creation_flags,
            env=child_environment,
        )
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def __enter__(self) -> "CodexAppServerClient":
        self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "xingzhi_voice_relay",
                    "title": "Xingzhi Voice Relay",
                    "version": "1.0.0",
                }
            },
        )
        self._notify("initialized", {})
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._process.stdin:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            self._process.wait(timeout=5)
        self._stdout_thread.join(timeout=2)
        self._stderr_thread.join(timeout=2)
        if self._process.stdout:
            self._process.stdout.close()
        if self._process.stderr:
            self._process.stderr.close()

    def deliver_turn(self, thread_id: str, cwd: str, text: str) -> str:
        resumed = self._request(
            "thread/resume",
            {
                "threadId": thread_id,
                "cwd": cwd,
                "modelProvider": os.environ.get("CODEX_MODEL_PROVIDER", "custom"),
                "model": os.environ.get("CODEX_MODEL", "gpt-5.6-sol"),
            },
        )
        resumed_id = resumed.get("thread", {}).get("id")
        if resumed_id != thread_id:
            raise CodexAppServerError(
                f"thread/resume returned unexpected thread id: {resumed_id!r}"
            )
        started = self._request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": text}],
                "cwd": cwd,
            },
        )
        turn_id = started.get("turn", {}).get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise CodexAppServerError("turn/start did not return a turn id")

        while True:
            message = self._next_message()
            if message.get("method") != "turn/completed":
                continue
            turn = message.get("params", {}).get("turn", {})
            if turn.get("id") != turn_id:
                continue
            status = turn.get("status")
            if status != "completed":
                raise CodexAppServerError(
                    f"voice turn {turn_id} finished with status {status!r}: {turn!r}"
                )
            return turn_id

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._write({"method": method, "id": request_id, "params": params})
        while True:
            message = self._next_message()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise CodexAppServerError(f"{method} failed: {message['error']!r}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise CodexAppServerError(f"{method} returned an invalid result")
            return result

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def _write(self, message: dict[str, Any]) -> None:
        if self._process.stdin is None:
            raise CodexAppServerError("app-server stdin is unavailable")
        self._process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._process.stdin.flush()

    def _next_message(self) -> dict[str, Any]:
        try:
            item = self._messages.get(timeout=self._timeout)
        except queue.Empty as exc:
            raise CodexAppServerError(
                f"app-server did not respond within {self._timeout:.1f} seconds"
            ) from exc
        if isinstance(item, BaseException):
            details = "".join(self._stderr_lines[-20:]).strip()
            raise CodexAppServerError(f"app-server stream ended: {item}; {details}")
        return item

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        try:
            for line in self._process.stdout:
                line = line.strip()
                if line:
                    self._messages.put(json.loads(line))
            self._messages.put(CodexAppServerError("stdout closed"))
        except BaseException as exc:
            self._messages.put(exc)

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            self._stderr_lines.append(line)


def deliver_voice_turn(
    command: Sequence[str], thread_id: str, cwd: str, text: str, timeout: float = 900.0
) -> str:
    with CodexAppServerClient(command, timeout) as client:
        return client.deliver_turn(thread_id, cwd, text)
