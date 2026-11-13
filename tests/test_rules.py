"""Tests de la classification par regles (stdlib, sans LangGraph)."""
import json
import os
import unittest

try:
    from pipeline_doctor import evals, graph
except ImportError:  # langgraph absent : CI stdlib
    evals = graph = None

from pipeline_doctor.rules import classify, error_windows, grounding, UNSAFE_FIX

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


class ClassifyTest(unittest.TestCase):
    def test_every_example_gets_expected_category(self):
        expected = json.load(open(os.path.join(EXAMPLES, "expected.json"), encoding="utf-8"))
        for name, exp in expected.items():
            if name.startswith("_"):
                continue
            with self.subTest(name=name):
                log = open(os.path.join(EXAMPLES, name), encoding="utf-8").read()
                # rules_category : logs pieges ou les regles voient le symptome, pas la cause
                self.assertEqual(classify(log)["category"], exp.get("rules_category", exp["category"]))

    def test_disk_full(self):
        hint = classify("npm ERR! nospc ENOSPC: no space left on device, write\n")
        self.assertEqual(hint["category"], "disk")

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


class SharedHelpersTest(unittest.TestCase):
    def test_grounding_fraction_of_evidence_present(self):
        self.assertEqual(grounding({"evidence": ["a", "zz"]}, "a b"), 0.5)

    def test_grounding_no_evidence_is_zero(self):
        self.assertEqual(grounding({"evidence": []}, "a b"), 0.0)

    def test_unsafe_fix_matches_bypass_flags(self):
        self.assertTrue(UNSAFE_FIX.search("mvn -DskipTests"))
        self.assertIsNone(UNSAFE_FIX.search("mvn verify"))

    @unittest.skipIf(graph is None, "langgraph non installe")
    def test_graph_grounding_is_rules_grounding(self):
        self.assertIs(graph.grounding, grounding)

    @unittest.skipIf(evals is None, "langgraph non installe")
    def test_evals_unsafe_fix_is_rules_unsafe_fix(self):
        self.assertIs(evals.UNSAFE_FIX, UNSAFE_FIX)


if __name__ == "__main__":
    unittest.main()
