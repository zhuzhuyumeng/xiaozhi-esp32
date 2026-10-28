import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "codex_app_server_client.py"
SPEC = importlib.util.spec_from_file_location("codex_app_server_client", MODULE_PATH)
client_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(client_module)


class StubClient(client_module.CodexAppServerClient):
    def __init__(self):
        self.requests = []
        self.messages = [
            {"method": "turn/completed", "params": {"turn": {"id": "turn", "status": "completed"}}}
        ]

    def _request(self, method, params):
        self.requests.append((method, params))
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        return {"turn": {"id": "turn"}}

    def _next_message(self):
        return self.messages.pop(0)


class CodexAppServerClientTest(unittest.TestCase):
    def test_resume_uses_configured_provider_and_model(self):
        client = StubClient()
        with patch.dict(
            os.environ,
            {"CODEX_MODEL_PROVIDER": "custom", "CODEX_MODEL": "gpt-test"},
        ):
            self.assertEqual(client.deliver_turn("thread", "F:\\work", "你好"), "turn")

        method, params = client.requests[0]
        self.assertEqual(method, "thread/resume")
        self.assertEqual(params["modelProvider"], "custom")
        self.assertEqual(params["model"], "gpt-test")
        self.assertEqual(params["cwd"], "F:\\work")


if __name__ == "__main__":
    unittest.main()
