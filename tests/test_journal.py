"""Tests du journal SQLite : idempotence et enregistrement."""
import json
import os
import tempfile
import unittest

from pipeline_doctor import journal


class JournalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "diag.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_and_is_processed(self):
        conn = journal.init(self.db)
        self.assertFalse(journal.is_processed(conn, 42))
        journal.record(conn, 42, "o/r", "ci.yml", "main",
                       {"cause": "c", "confidence": 0.8, "fix": "f", "evidence": ["e"]},
                       "diagnosed")
        self.assertTrue(journal.is_processed(conn, 42))
        conn.close()

    def test_record_replaces(self):
        conn = journal.init(self.db)
        journal.record(conn, 1, "o/r", "ci.yml", "main", None, "llm_error")
        journal.record(conn, 1, "o/r", "ci.yml", "main",
                       {"cause": "c", "confidence": 0.5, "fix": "f", "evidence": []},
                       "diagnosed")
        status, cause = conn.execute(
            "SELECT status, cause FROM diagnostics WHERE run_id=1").fetchone()
        self.assertEqual(status, "diagnosed")
        self.assertEqual(cause, "c")
        conn.close()

    def test_evidence_stored_as_json(self):
        conn = journal.init(self.db)
        journal.record(conn, 7, "o/r", "ci.yml", "main",
                       {"cause": "c", "confidence": 0.8, "fix": "f", "evidence": ["a", "b"]},
                       "diagnosed")
        row = conn.execute("SELECT evidence FROM diagnostics WHERE run_id=7").fetchone()
        self.assertEqual(json.loads(row[0]), ["a", "b"])
        conn.close()

    def test_is_processed_without_record(self):
        conn = journal.init(self.db)
        self.assertFalse(journal.is_processed(conn, 999))
        conn.close()


if __name__ == "__main__":
    unittest.main()
