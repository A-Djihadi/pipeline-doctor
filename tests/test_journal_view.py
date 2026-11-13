"""Tests de la couche lecture seule du journal (T1 : requetes)."""
import unittest

from pipeline_doctor import journal
from pipeline_doctor import journal_view


class ListDiagnosticsTest(unittest.TestCase):
    def _conn(self):
        return journal.init(":memory:")

    def test_orders_newest_first(self):
        conn = self._conn()
        for run_id in (1, 2, 3):
            journal.record(conn, run_id, "a/b", "ci.yml", "main",
                           {"cause": f"c{run_id}", "confidence": 0.5,
                            "fix": "f", "evidence": []},
                           "diagnosed")
        conn.execute("UPDATE diagnostics SET processed_at='2026-11-01T00:00:00Z' WHERE run_id=1")
        conn.execute("UPDATE diagnostics SET processed_at='2026-11-02T00:00:00Z' WHERE run_id=2")
        conn.execute("UPDATE diagnostics SET processed_at='2026-11-03T00:00:00Z' WHERE run_id=3")
        conn.commit()
        rows = journal_view.list_diagnostics(conn)
        self.assertEqual([r["run_id"] for r in rows], [3, 2, 1])
        conn.close()

    def test_filters_by_repo(self):
        conn = self._conn()
        journal.record(conn, 1, "a/b", "ci.yml", "main", None, "llm_error")
        journal.record(conn, 2, "x/y", "ci.yml", "main", None, "llm_error")
        rows = journal_view.list_diagnostics(conn, repo="a/b")
        self.assertEqual([r["repo"] for r in rows], ["a/b"])
        conn.close()

    def test_filters_by_status(self):
        conn = self._conn()
        journal.record(conn, 1, "a/b", "ci.yml", "main", None, "llm_error")
        journal.record(conn, 2, "a/b", "ci.yml", "main",
                       {"cause": "c", "confidence": 0.5, "fix": "f", "evidence": []},
                       "diagnosed")
        rows = journal_view.list_diagnostics(conn, status="llm_error")
        self.assertEqual([r["status"] for r in rows], ["llm_error"])
        conn.close()

    def test_limit(self):
        conn = self._conn()
        for run_id in (1, 2, 3, 4, 5):
            journal.record(conn, run_id, "a/b", "ci.yml", "main", None, "llm_error")
        rows = journal_view.list_diagnostics(conn, limit=2)
        self.assertEqual(len(rows), 2)
        conn.close()


class GetDiagnosticTest(unittest.TestCase):
    def _conn(self):
        return journal.init(":memory:")

    def test_returns_decoded_dict(self):
        conn = self._conn()
        journal.record(conn, 101, "a/b", "ci.yml", "main",
                       {"cause": "c", "confidence": 0.9, "fix": "f", "evidence": ["e1", "e2"]},
                       "diagnosed")
        row = journal_view.get_diagnostic(conn, 101)
        self.assertIsInstance(row, dict)
        self.assertEqual(row["evidence"], ["e1", "e2"])
        self.assertEqual(row["confidence"], 0.9)
        conn.close()

    def test_unknown_run_returns_none(self):
        conn = self._conn()
        self.assertIsNone(journal_view.get_diagnostic(conn, 999999))
        conn.close()

    def test_malformed_evidence_yields_empty_list(self):
        conn = self._conn()
        conn.execute(
            "INSERT INTO diagnostics (run_id, repo, status, processed_at, evidence) "
            "VALUES (5, 'a/b', 'diagnosed', 't', 'not json')"
        )
        conn.commit()
        row = journal_view.get_diagnostic(conn, 5)
        self.assertEqual(row["evidence"], [])
        conn.close()

    def test_null_confidence_stays_none(self):
        conn = self._conn()
        journal.record(conn, 6, "a/b", "ci.yml", "main", None, "llm_error")
        row = journal_view.get_diagnostic(conn, 6)
        self.assertIsNone(row["confidence"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
