"""Tests de la classification par regles (stdlib, sans LangGraph)."""
import json
import os
import unittest

from pipeline_doctor.rules import classify, error_windows

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


class ClassifyTest(unittest.TestCase):
    def test_every_example_gets_expected_category(self):
        expected = json.load(open(os.path.join(EXAMPLES, "expected.json"), encoding="utf-8"))
        for name, exp in expected.items():
            if name.startswith("_"):
                continue
            with self.subTest(name=name):
                log = open(os.path.join(EXAMPLES, name), encoding="utf-8").read()
                self.assertEqual(classify(log)["category"], exp["category"])

    def test_evidence_is_copied_from_log(self):
        log = "step 1\n[ERROR] COMPILATION ERROR :\nstep 3"
        hint = classify(log)
        self.assertEqual(hint["evidence"], ["[ERROR] COMPILATION ERROR :"])

    def test_unknown_when_no_signature(self):
        hint = classify("tout va bien\nexit 0")
        self.assertEqual(hint["category"], "unknown")
        self.assertLess(hint["confidence"], 0.5)

    def test_network_wins_over_dependency(self):
        log = "Connection to pypi.org timed out\nERROR: No matching distribution found for x"
        self.assertEqual(classify(log)["category"], "network")


class ErrorWindowsTest(unittest.TestCase):
    def test_keeps_lines_around_errors_only(self):
        lines = [f"info {i}" for i in range(50)]
        lines[25] = "ERROR boom"
        out = error_windows("\n".join(lines), radius=1)
        self.assertIn("ERROR boom", out)
        self.assertIn("info 24", out)
        self.assertNotIn("info 10", out)

    def test_falls_back_to_tail(self):
        self.assertEqual(error_windows("a\nb\nc", max_chars=3), "b\nc"[-3:])


if __name__ == "__main__":
    unittest.main()
