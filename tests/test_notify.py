"""Tests de la notification : selection des jobs en echec + payload Slack."""
import contextlib
import io
import os
import unittest
from unittest import mock

from pipeline_doctor import notify
from pipeline_doctor.github import failed_job_ids, run_url
from pipeline_doctor.slack import MAX_BLOCK_CHARS, build_payload

# Faux secret assemble a l'execution (jamais de litteral ressemblant a une cle,
# sinon gitleaks + push protection le signaleraient).
FAKE_GITHUB_TOKEN = "ghp_" + "A" * 24


class FailedJobIdsTest(unittest.TestCase):
    def test_selects_only_failed(self):
        jobs = [
            {"id": 1, "conclusion": "success"},
            {"id": 2, "conclusion": "failure"},
            {"id": 3, "conclusion": "failure"},
            {"id": 4, "conclusion": "cancelled"},
            {"id": 5, "conclusion": None},
        ]
        self.assertEqual(failed_job_ids(jobs), [2, 3])

    def test_empty(self):
        self.assertEqual(failed_job_ids([]), [])


class RunUrlTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(
            run_url("A-Djihadi/pipeline-doctor", "42"),
            "https://github.com/A-Djihadi/pipeline-doctor/actions/runs/42",
        )


class BuildPayloadTest(unittest.TestCase):
    def _log(self):
        return "A" * 10000 + "ERREUR_ICI"

    def test_excerpt_is_tail_and_bounded(self):
        payload = build_payload(
            "A-Djihadi/pipeline-doctor", "CI", "main", "42",
            "https://github.com/A-Djihadi/pipeline-doctor/actions/runs/42",
            3, self._log(),
        )
        last_block = payload["blocks"][-1]["text"]["text"]
        self.assertLessEqual(len(last_block), MAX_BLOCK_CHARS + 6)  # + les fences ```
        self.assertTrue(last_block.endswith("ERREUR_ICI```"))
        self.assertTrue(last_block.startswith("```"))

    def test_metadata_in_fields(self):
        payload = build_payload(
            "A-Djihadi/pipeline-doctor", "CI", "main", "42",
            "https://example.org/run/42", 0, "log",
        )
        fields = [
            f["text"]
            for b in payload["blocks"]
            if b["type"] == "section" and "fields" in b
            for f in b["fields"]
        ]
        joined = "\n".join(fields)
        self.assertIn("CI", joined)
        self.assertIn("main", joined)
        self.assertIn("<https://example.org/run/42|#42>", joined)


class NotifyMainTest(unittest.TestCase):
    _VARS = ("GITHUB_REPOSITORY", "GITHUB_TOKEN", "PD_RUN_ID", "PD_RUN_NAME",
             "PD_BRANCH", "SLACK_WEBHOOK_URL")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._VARS}
        for k in self._VARS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_missing_vars_returns_2(self):
        self.assertEqual(notify.main([]), 2)

    def test_no_failed_job_skips(self):
        os.environ["GITHUB_REPOSITORY"] = "o/r"
        os.environ["GITHUB_TOKEN"] = "t"
        os.environ["PD_RUN_ID"] = "1"
        with mock.patch.object(notify, "list_jobs", return_value=[]):
            self.assertEqual(notify.main([]), 0)

    def test_dry_run_masks_secret_in_payload(self):
        os.environ["GITHUB_REPOSITORY"] = "o/r"
        os.environ["GITHUB_TOKEN"] = "t"
        os.environ["PD_RUN_ID"] = "1"
        os.environ["PD_RUN_NAME"] = "CI"
        os.environ["PD_BRANCH"] = "main"
        jobs = [{"id": 7, "conclusion": "failure"}]
        fake_log = "boom " + FAKE_GITHUB_TOKEN + " fin"
        buf = io.StringIO()
        with mock.patch.object(notify, "list_jobs", return_value=jobs), \
                mock.patch.object(notify, "job_log", return_value=fake_log), \
                contextlib.redirect_stdout(buf):
            self.assertEqual(notify.main(["--dry-run"]), 0)
        out = buf.getvalue()
        self.assertIn("[REDACTED:github_token]", out)
        self.assertNotIn("ghp_", out)


if __name__ == "__main__":
    unittest.main()
