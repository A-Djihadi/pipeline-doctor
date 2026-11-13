"""Tests du client Ollama : mise en forme du message utilisateur (user_template)."""
import json
import unittest
from unittest import mock

from pipeline_doctor import llm


def _response():
    class _Resp:
        def read(self):
            return json.dumps({"message": {"content": "{}"},
                               "prompt_eval_count": 1, "eval_count": 1}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp()


class ChatTest(unittest.TestCase):
    def _user_content(self, log, **kwargs):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["payload"] = json.loads(req.data.decode())
            return _response()

        with mock.patch.object(llm.urllib.request, "urlopen", side_effect=fake_urlopen):
            llm.chat("system", log, **kwargs)
        return captured["payload"]["messages"][1]["content"]

    def test_default_template_prefixes_the_log(self):
        self.assertEqual(self._user_content("X"), "Log du build en echec :\n\nX")

    def test_custom_template_sends_log_verbatim(self):
        self.assertEqual(self._user_content("X", user_template="{log}"), "X")

    def test_log_braces_are_not_formatted(self):
        # Le log lui-meme n'est jamais passe a .format : ses accolades survivent.
        self.assertEqual(self._user_content("value {nope}", user_template="{log}"), "value {nope}")


if __name__ == "__main__":
    unittest.main()
