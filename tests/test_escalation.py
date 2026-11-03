"""Tests de la politique d'escalade cloud (session 4) : confiance basse -> DeepSeek."""
import os
import tempfile
import unittest
from unittest import mock

from pipeline_doctor import agent, journal


class EscalationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "diag.db")
        self.repo = "A-Djihadi/pipeline-doctor"
        self.token = "t"
        self.failed_run = {"id": 101, "name": "CI", "head_branch": "main", "conclusion": "failure"}
        self.failed_job = {"id": 7, "conclusion": "failure"}

    def tearDown(self):
        self._tmp.cleanup()

    def _diagnosis(self, cause="cause x", confidence=0.9):
        return {"cause": cause, "evidence": ["ligne 1"], "fix": "fix f", "confidence": confidence}

    def _poll(self, ollama, deepseek=None, deepseek_error=None, api_key=""):
        build_calls = []

        def fake_build(*args, **kwargs):
            build_calls.append(kwargs)
            return {"blocks": []}

        ds_kwargs = {}
        if deepseek_error is not None:
            ds_kwargs["side_effect"] = deepseek_error
        else:
            ds_kwargs["return_value"] = deepseek

        with mock.patch.object(agent, "list_runs", return_value=[self.failed_run]), \
                mock.patch.object(agent, "list_jobs", return_value=[self.failed_job]), \
                mock.patch.object(agent, "job_log", return_value="log line"), \
                mock.patch.object(agent, "send"), \
                mock.patch.object(agent, "ask_ollama", return_value=ollama), \
                mock.patch.object(agent, "ask_deepseek", **ds_kwargs) as fake_ds, \
                mock.patch.object(agent, "build_diagnosis_payload", side_effect=fake_build), \
                mock.patch.dict(os.environ, {"PD_DEEPSEEK_API_KEY": api_key}):
            agent.poll_once(self.repo, self.token, "ci.yml", self.db,
                            "https://hooks.example/x")
        return fake_ds, build_calls

    def _provider(self, run_id=101):
        conn = journal.init(self.db)
        row = conn.execute(
            "SELECT provider, cause FROM diagnostics WHERE run_id=?", (run_id,)).fetchone()
        conn.close()
        return row

    def test_high_confidence_no_escalation(self):
        fake_ds, build_calls = self._poll(self._diagnosis(confidence=0.9), deepseek=self._diagnosis(cause="c2", confidence=0.95))
        fake_ds.assert_not_called()
        provider, cause = self._provider()
        self.assertEqual(provider, "ollama")
        self.assertEqual(cause, "cause x")
        self.assertEqual(build_calls[0]["provider"], "ollama")

    def test_low_confidence_with_key_escalates(self):
        deepseek = self._diagnosis(cause="c2", confidence=0.95)
        fake_ds, build_calls = self._poll(self._diagnosis(confidence=0.3), deepseek=deepseek, api_key="k")
        fake_ds.assert_called_once()
        provider, cause = self._provider()
        self.assertEqual(provider, "deepseek")
        self.assertEqual(cause, "c2")
        self.assertEqual(build_calls[0]["provider"], "deepseek")

    def test_low_confidence_without_key_keeps_ollama(self):
        fake_ds, build_calls = self._poll(self._diagnosis(confidence=0.3), deepseek=self._diagnosis(cause="c2"))
        fake_ds.assert_not_called()
        provider, cause = self._provider()
        self.assertEqual(provider, "ollama")
        self.assertEqual(cause, "cause x")
        self.assertEqual(build_calls[0]["provider"], "ollama")

    def test_deepseek_failure_falls_back_to_ollama(self):
        def boom(log):
            raise RuntimeError("boom")

        fake_ds, build_calls = self._poll(self._diagnosis(confidence=0.3), deepseek_error=boom, api_key="k")
        fake_ds.assert_called_once()
        provider, cause = self._provider()
        self.assertEqual(provider, "ollama")
        self.assertEqual(cause, "cause x")
        self.assertEqual(build_calls[0]["provider"], "ollama")

    def test_missing_confidence_escalates(self):
        ollama = {"cause": "cause x", "evidence": ["ligne 1"], "fix": "fix f"}
        deepseek = self._diagnosis(cause="c2", confidence=0.95)
        fake_ds, build_calls = self._poll(ollama, deepseek=deepseek, api_key="k")
        fake_ds.assert_called_once()
        provider, cause = self._provider()
        self.assertEqual(provider, "deepseek")
        self.assertEqual(cause, "c2")


if __name__ == "__main__":
    unittest.main()
