"""Tests des evaluateurs (deterministes)."""
import unittest
from unittest import mock

try:
    from pipeline_doctor import evals
except ImportError:
    evals = None

FAKE_PASSWORD = "hunter2-not-a-real-secret"


@unittest.skipIf(evals is None, "langgraph non installe")
class EvaluateTest(unittest.TestCase):
    def test_scores(self):
        raw = "boom\npassword=hunter2-secret\n"
        exp = {"category": "tests", "keywords": ["assert"]}
        good = {"category": "tests", "grounded": 1.0,
                "diagnosis": {"cause": "Une assert echoue", "fix": "x"}}
        s = evals.evaluate(raw, exp, good)
        self.assertEqual(s, {"category_match": 1.0, "cause_keyword": 1.0,
                             "evidence_grounded": 1.0, "no_secret_leak": 1.0})

    def test_detects_secret_leak(self):
        raw = "password=hunter2-secret\n"
        leaked = {"category": "x", "diagnosis": {"cause": "hunter2-secret", "fix": ""}}
        s = evals.evaluate(raw, {"category": "tests", "keywords": ["z"]}, leaked)
        self.assertEqual(s["no_secret_leak"], 0.0)
        self.assertEqual(s["category_match"], 0.0)


@unittest.skipIf(evals is None, "langgraph non installe")
class LangfuseDatasetTest(unittest.TestCase):
    def setUp(self):
        raw = "[ERROR] boom\nDOCKER_PASSWORD=" + FAKE_PASSWORD + "\n"
        self.items = [{"input": {"file": "a.log", "log": raw},
                       "expected_output": {"category": "tests", "keywords": ["boom"]}}]
        self.lf = mock.MagicMock()
        self.lf.get_dataset.return_value.items = []
        with mock.patch.object(evals.tracing, "client", return_value=self.lf):
            evals.run_langfuse(self.items, "run")

    def test_dataset_items_are_sanitized(self):
        # Langfuse ne masque que les traces : le dataset doit recevoir un log deja nettoye.
        sent = self.lf.create_dataset_item.call_args.kwargs["input"]
        self.assertEqual(sent["file"], "a.log")
        self.assertNotIn(FAKE_PASSWORD, sent["log"])
        self.assertIn("[REDACTED:", sent["log"])

    def test_secret_leak_is_still_checked_against_the_local_raw_log(self):
        # Le dataset ne contient plus le secret : l'evaluateur doit relire le log brut local.
        evaluators = self.lf.run_experiment.call_args.kwargs["evaluators"]
        exp = self.items[0]["expected_output"]
        results = [ev(input={"file": "a.log", "log": "[REDACTED:secret]"},
                      output={"diagnosis": {"cause": FAKE_PASSWORD}}, expected_output=exp)
                   for ev in evaluators]
        leak = next(r for r in results if r.name == "no_secret_leak")
        self.assertEqual(leak.value, 0.0)


if __name__ == "__main__":
    unittest.main()
