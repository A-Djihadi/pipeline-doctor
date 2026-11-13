"""Tests v2 du journal : tables additives jenkins_diagnostics et fix_proposals."""
import json
import os
import sqlite3
import tempfile
import unittest

from pipeline_doctor import journal


class JournalV2Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "diag.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _tables(self, conn):
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    def test_init_creates_all_three_tables(self):
        conn = journal.init(":memory:")
        tables = self._tables(conn)
        self.assertIn("diagnostics", tables)
        self.assertIn("jenkins_diagnostics", tables)
        self.assertIn("fix_proposals", tables)
        conn.close()

    def test_v1_db_gains_tables_and_keeps_row(self):
        # Base v1 creee a la main : seule la table diagnostics + une ligne.
        conn = sqlite3.connect(self.db)
        conn.execute(
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
        conn.execute(
            "INSERT INTO diagnostics VALUES "
            "(1, 'o/r', 'ci.yml', 'main', NULL, NULL, NULL, '[]', 'diagnosed', 't')"
        )
        conn.commit()
        conn.close()

        conn = journal.init(self.db)
        self.assertIn("jenkins_diagnostics", self._tables(conn))
        self.assertIn("fix_proposals", self._tables(conn))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM diagnostics").fetchone()[0], 1)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(diagnostics)")]
        self.assertEqual(cols, ["run_id", "repo", "workflow", "branch", "cause",
                                "confidence", "fix", "evidence", "status", "processed_at"])
        conn.close()

    def test_is_build_processed_and_record_build(self):
        conn = journal.init(self.db)
        self.assertFalse(journal.is_build_processed(conn, "https://j/job/a/7/"))
        journal.record_build(conn, "https://j/job/a/7/", "https://j", "team/app", 7,
                             {"cause": "c", "evidence": ["e"]}, "diagnosed")
        self.assertTrue(journal.is_build_processed(conn, "https://j/job/a/7/"))
        conn.close()

    def test_record_build_none_diagnosis(self):
        conn = journal.init(self.db)
        journal.record_build(conn, "k", "https://j", "team/app", 7, None, "llm_error")
        row = conn.execute(
            "SELECT cause, confidence, fix, evidence, status "
            "FROM jenkins_diagnostics WHERE build_key='k'"
        ).fetchone()
        self.assertIsNone(row[0])  # cause
        self.assertIsNone(row[1])  # confidence
        self.assertIsNone(row[2])  # fix
        self.assertEqual(json.loads(row[3]), [])  # evidence "[]"
        self.assertEqual(row[4], "llm_error")
        conn.close()

    def test_record_fix_replaces_same_source_ref(self):
        conn = journal.init(self.db)
        row1 = {"source": "github", "source_ref": "101", "repo": "o/r",
                "base_branch": "main", "base_sha": "abc", "head_branch": None,
                "status": "proposed", "reason": None, "files": ["a.py"], "diff": "-",
                "pr_number": None, "pr_url": None, "model": "m", "prompt_version": "v1"}
        row2 = dict(row1, status="pr_opened", pr_number=1, pr_url="https://x")
        journal.record_fix(conn, row1)
        journal.record_fix(conn, row2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM fix_proposals").fetchone()[0], 1)
        status = conn.execute("SELECT status FROM fix_proposals").fetchone()[0]
        self.assertEqual(status, "pr_opened")
        conn.close()

    def test_fixes_since_counts_only_proposed_and_pr_opened(self):
        conn = journal.init(self.db)

        def insert(status, repo, created_at, source_ref):
            conn.execute(
                "INSERT INTO fix_proposals (source, source_ref, repo, status, created_at) "
                "VALUES ('github', ?, ?, ?, ?)",
                (source_ref, repo, status, created_at),
            )

        insert("proposed", "o/r", "2026-11-13T00:00:00Z", "1")
        insert("pr_opened", "o/r", "2026-11-13T01:00:00Z", "2")
        insert("skipped", "o/r", "2026-11-13T02:00:00Z", "3")
        insert("rejected", "o/r", "2026-11-13T03:00:00Z", "4")
        insert("proposed", "other/r", "2026-11-13T04:00:00Z", "5")
        insert("proposed", "o/r", "2026-11-10T00:00:00Z", "6")  # ancien
        conn.commit()
        self.assertEqual(journal.fixes_since(conn, "o/r", "2026-11-13T00:00:00Z"), 2)
        conn.close()


if __name__ == "__main__":
    unittest.main()
