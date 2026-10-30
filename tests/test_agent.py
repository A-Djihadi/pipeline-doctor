"""Tests de l'agent auto-heberge : poll, idempotence, masquage, orchestration."""
import os
import tempfile
import unittest
from unittest import mock

from pipeline_doctor import agent, journal

# Faux secret assemble a l'execution (jamais de litteral ressemblant a une cle).
FAKE_GITHUB_TOKEN = "ghp_" + "A" * 24


class PollOnceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "diag.db")
        self.repo = "A-Djihadi/pipeline-doctor"
        self.token = "t"
        self.failed_run = {"id": 101, "name": "CI", "head_branch": "main", "conclusion": "failure"}
        self.failed_job = {"id": 7, "conclusion": "failure"}

    def tearDown(self):
        self._tmp.cleanup()

    def _diagnosis(self):
        return {"cause": "cause x", "evidence": ["ligne 1"], "fix": "fix f", "confidence": 0.9}

    def test_processes_failed_run_and_records(self):
        log = "boom " + FAKE_GITHUB_TOKEN + " fin"
        seen = {}

        def fake_ask(cleaned):
            seen["log"] = cleaned
            return self._diagnosis()

        with mock.patch.object(agent, "list_runs", return_value=[self.failed_run]), \
                mock.patch.object(agent, "list_jobs", return_value=[self.failed_job]), \
                mock.patch.object(agent, "job_log", return_value=log), \
                mock.patch.object(agent, "ask_ollama", side_effect=fake_ask), \
                mock.patch.object(agent, "send") as fake_send:
            n = agent.poll_once(self.repo, self.token, "ci.yml", self.db,
                                "https://hooks.example/x")
        self.assertEqual(n, 1)
        self.assertNotIn(FAKE_GITHUB_TOKEN, seen["log"])  # sanitize avant Ollama
        self.assertIn("[REDACTED:github_token]", seen["log"])
        fake_send.assert_called_once()
        conn = journal.init(self.db)
        self.assertTrue(journal.is_processed(conn, 101))
        conn.close()

    def test_skips_already_processed(self):
        conn = journal.init(self.db)
        journal.record(conn, 101, self.repo, "ci.yml", "main", self._diagnosis(), "diagnosed")
        conn.close()
        with mock.patch.object(agent, "list_runs", return_value=[self.failed_run]), \
                mock.patch.object(agent, "send") as fake_send:
            n = agent.poll_once(self.repo, self.token, "ci.yml", self.db,
                                "https://hooks.example/x")
        self.assertEqual(n, 0)
        fake_send.assert_not_called()

    def test_skips_success_runs(self):
        ok = {"id": 102, "name": "CI", "head_branch": "main", "conclusion": "success"}
        with mock.patch.object(agent, "list_runs", return_value=[ok]), \
                mock.patch.object(agent, "send") as fake_send, \
                mock.patch.object(agent, "list_jobs") as fake_jobs:
            n = agent.poll_once(self.repo, self.token, "ci.yml", self.db,
                                "https://hooks.example/x")
        self.assertEqual(n, 0)
        fake_send.assert_not_called()
        fake_jobs.assert_not_called()

    def test_no_webhook_still_records(self):
        log = "boom " + FAKE_GITHUB_TOKEN + " fin"
        with mock.patch.object(agent, "list_runs", return_value=[self.failed_run]), \
                mock.patch.object(agent, "list_jobs", return_value=[self.failed_job]), \
                mock.patch.object(agent, "job_log", return_value=log), \
                mock.patch.object(agent, "ask_ollama", return_value=self._diagnosis()), \
                mock.patch.object(agent, "send") as fake_send:
            n = agent.poll_once(self.repo, self.token, "ci.yml", self.db, "")
        self.assertEqual(n, 1)
        fake_send.assert_not_called()
        conn = journal.init(self.db)
        self.assertTrue(journal.is_processed(conn, 101))
        conn.close()


if __name__ == "__main__":
    unittest.main()
