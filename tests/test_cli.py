"""Tests du sous-commandement journal et de la compatibilite CLI (T4)."""
import contextlib
import io
import os
import tempfile
import unittest

from pipeline_doctor import cli, journal


class JournalCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "diag.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _seed(self):
        conn = journal.init(self.db)
        journal.record(conn, 101, "A-Djihadi/pipeline-doctor", "ci.yml", "main",
                       {"cause": "cause x", "confidence": 0.9, "fix": "fix f", "evidence": ["e1"]},
                       "diagnosed")
        journal.record(conn, 102, "A-Djihadi/pipeline-doctor", "ci.yml", "main", None, "llm_error")
        conn.close()

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_legacy_path_unchanged(self):
        rc, out, err = self._run(["examples/maven-compile-error.log", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("[sanitizer]", err)
        self.assertNotIn("RUN_ID", out)
        self.assertNotIn("No diagnostics recorded.", out)

    def test_empty_journal_lists_nothing(self):
        journal.init(self.db).close()  # schema, aucune ligne
        rc, out, err = self._run(["journal", "--db", self.db])
        self.assertEqual(rc, 0)
        self.assertIn("No diagnostics recorded.", out)

    def test_list_seeded(self):
        self._seed()
        rc, out, err = self._run(["journal", "--db", self.db])
        self.assertEqual(rc, 0)
        self.assertIn("101", out)
        self.assertIn("cause x", out)

    def test_show_run(self):
        self._seed()
        rc, out, err = self._run(["journal", "show", "101", "--db", self.db])
        self.assertEqual(rc, 0)
        self.assertIn("cause x", out)
        self.assertIn("e1", out)

    def test_show_unknown_run(self):
        self._seed()
        rc, out, err = self._run(["journal", "show", "999999", "--db", self.db])
        self.assertEqual(rc, 1)
        self.assertIn("999999", err)

    def test_html_output(self):
        self._seed()
        rc, out, err = self._run(["journal", "--db", self.db, "--html"])
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("<!doctype html>"))

    def test_html_out_file(self):
        self._seed()
        out_path = os.path.join(self._tmp.name, "journal.html")
        rc, out, err = self._run(["journal", "--db", self.db, "--html", "--out", out_path])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(out_path))
        with open(out_path, encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("<!doctype html>"))

    def test_out_without_html_rejected(self):
        out_path = os.path.join(self._tmp.name, "journal.html")
        rc, out, err = self._run(["journal", "--db", self.db, "--out", out_path])
        self.assertEqual(rc, 2)

    def test_limit_zero_rejected(self):
        rc, out, err = self._run(["journal", "--db", self.db, "--limit", "0"])
        self.assertEqual(rc, 2)

    def test_missing_db_is_empty(self):
        rc, out, err = self._run(["journal", "--db", self.db])
        self.assertEqual(rc, 0)
        self.assertIn("No diagnostics recorded.", out)

    def test_lists_both_and_filters_status(self):
        self._seed()
        rc, out, err = self._run(["journal", "--db", self.db])
        self.assertEqual(rc, 0)
        self.assertIn("101", out)
        self.assertIn("102", out)
        self.assertIn("llm_error", out)

        rc2, out2, err2 = self._run(["journal", "--db", self.db, "--status", "llm_error"])
        self.assertEqual(rc2, 0)
        self.assertIn("102", out2)
        self.assertNotIn("101", out2)

    def test_show_llm_error_renders_dash_cause(self):
        self._seed()
        rc, out, err = self._run(["journal", "show", "102", "--db", self.db])
        self.assertEqual(rc, 0)
        self.assertIn("cause", out)
        self.assertIn(": -", out)


if __name__ == "__main__":
    unittest.main()
