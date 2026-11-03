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


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "diag.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_provider_is_ollama(self):
        conn = journal.init(self.db)
        journal.record(conn, 1, "o/r", "ci.yml", "main",
                       {"cause": "c", "confidence": 0.8, "fix": "f", "evidence": []},
                       "diagnosed")
        provider = conn.execute("SELECT provider FROM diagnostics WHERE run_id=1").fetchone()[0]
        conn.close()
        self.assertEqual(provider, "ollama")

    def test_record_deepseek_provider(self):
        conn = journal.init(self.db)
        journal.record(conn, 2, "o/r", "ci.yml", "main",
                       {"cause": "c", "confidence": 0.8, "fix": "f", "evidence": []},
                       "diagnosed", provider="deepseek")
        provider = conn.execute("SELECT provider FROM diagnostics WHERE run_id=2").fetchone()[0]
        conn.close()
        self.assertEqual(provider, "deepseek")

    def test_migration_adds_column_on_existing_db(self):
        import sqlite3
        # Base heritee d'une session precedente (sans colonne `provider`).
        legacy = sqlite3.connect(self.db)
        legacy.execute(
            """
            CREATE TABLE diagnostics (
                run_id       INTEGER PRIMARY KEY,
                repo         TEXT NOT NULL,
                workflow     TEXT,
                branch       TEXT,
                cause        TEXT,
                confidence   REAL,
                fix          TEXT,
                evidence     TEXT,
                status       TEXT NOT NULL,
                processed_at TEXT NOT NULL
            )
            """
        )
        legacy.commit()
        legacy.close()

        conn = journal.init(self.db)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(diagnostics)")]
        self.assertIn("provider", cols)

        # La migration est idempotente : un second init n'echoue pas.
        conn2 = journal.init(self.db)
        conn2.close()

        journal.record(conn, 3, "o/r", "ci.yml", "main", None, "llm_error")
        provider = conn.execute("SELECT provider FROM diagnostics WHERE run_id=3").fetchone()[0]
        conn.close()
        self.assertEqual(provider, "ollama")


if __name__ == "__main__":
    unittest.main()
