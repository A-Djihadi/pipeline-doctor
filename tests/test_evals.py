"""Tests des evaluateurs (deterministes)."""
import unittest

try:
    from pipeline_doctor import evals
except ImportError:
    evals = None


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


if __name__ == "__main__":
    unittest.main()
