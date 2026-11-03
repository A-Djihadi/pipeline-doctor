"""Tests du client DeepSeek (escalade cloud, stdlib uniquement)."""
import json
import os
import unittest
from unittest import mock

from pipeline_doctor import llm


class AskDeepSeekTest(unittest.TestCase):
    def _response(self, content):
        class _Resp:
            def __init__(self, body):
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return self._body

        body = json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()
        return _Resp(body)

    def test_sends_correct_request_and_parses(self):
        diagnosis = {"cause": "c", "evidence": ["e"], "fix": "f", "confidence": 0.8}
        captured = {}

        def fake_open(req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
            captured["payload"] = json.loads(req.data.decode())
            captured["timeout"] = timeout
            return self._response(diagnosis)

        with mock.patch.dict(os.environ, {"PD_DEEPSEEK_API_KEY": "test-key"}), \
                mock.patch.object(llm.urllib.request, "urlopen", side_effect=fake_open):
            result = llm.ask_deepseek("some log", timeout=60)

        self.assertEqual(result, diagnosis)
        self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(captured["headers"]["authorization"], "Bearer test-key")
        self.assertEqual(captured["headers"]["content-type"], "application/json")
        self.assertEqual(captured["timeout"], 60)

        payload = captured["payload"]
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("some log", payload["messages"][1]["content"])

    def test_missing_api_key_raises(self):
        with mock.patch.dict(os.environ, {"PD_DEEPSEEK_API_KEY": ""}):
            with self.assertRaises(ValueError):
                llm.ask_deepseek("some log")


if __name__ == "__main__":
    unittest.main()
