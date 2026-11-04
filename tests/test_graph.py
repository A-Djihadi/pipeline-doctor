"""Tests du graphe LangGraph : routage, re-essai, repli, validation humaine, masquage.

Ignores si l'extra n'est pas installe (pip install -r requirements-agent.txt).
"""
import os
import unittest
from unittest import mock

try:
    from pipeline_doctor import graph
except ImportError:  # langgraph absent : CI stdlib
    graph = None

FAKE_TOKEN = "ghp_" + "B" * 24
LOG = "[INFO] start\n[ERROR] COMPILATION ERROR :\nAuthorization: Bearer " + FAKE_TOKEN + "\n[INFO] BUILD FAILURE"
META = {"model": "qwen2.5-coder:3b", "model_parameters": {}, "usage": {"input": 10, "output": 5}}


def diag(conf, evidence=("[INFO] BUILD FAILURE",), category="compilation"):
    return {"category": category, "cause": "compilation", "evidence": list(evidence), "fix": "corriger",
            "confidence": conf}


@unittest.skipIf(graph is None, "langgraph non installe")
class GraphTest(unittest.TestCase):
    def setUp(self):
        os.environ["LANGFUSE_DISABLED"] = "1"

    def run_with(self, responses, **kw):
        calls = []

        def fake_chat(system, context, **_):
            calls.append(context)
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r, META

        with mock.patch.object(graph, "chat", side_effect=fake_chat):
            return graph.run(LOG, **kw), calls

    def test_high_confidence_goes_straight_to_finalize(self):
        res, calls = self.run_with([diag(0.9)])
        self.assertEqual(res["path"], ["sanitize", "classify", "diagnose", "finalize"])
        self.assertEqual(res["source"], "llm")
        self.assertEqual(len(calls), 1)

    def test_low_confidence_retries_once_with_focused_context(self):
        res, calls = self.run_with([diag(0.2), diag(0.3)])
        self.assertEqual(res["path"], ["sanitize", "classify", "diagnose", "focus_context",
                                       "diagnose", "finalize"])
        self.assertEqual(res["attempts"], 2)  # MAX_ATTEMPTS : pas de boucle infinie
        self.assertNotEqual(calls[0], calls[1])

    def test_ollama_down_falls_back_to_rules(self):
        res, _ = self.run_with([ConnectionRefusedError("refused")])
        self.assertEqual(res["path"][-2:], ["fallback_rules", "finalize"])
        self.assertEqual(res["source"], "rules")
        self.assertEqual(res["category"], "compilation")

    def test_secrets_never_reach_the_model(self):
        _, calls = self.run_with([diag(0.9)])
        self.assertNotIn(FAKE_TOKEN, calls[0])
        self.assertIn("[REDACTED:", calls[0])

    def test_grounding_detects_invented_evidence(self):
        invented = diag(0.9, evidence=("[INFO] BUILD FAILURE", "ligne inventee"))
        res, _ = self.run_with([invented, dict(invented)])
        self.assertEqual(res["grounded"], 0.5)

    def test_invented_evidence_triggers_retry_despite_high_confidence(self):
        # La confiance auto-declaree reste a 0.9 meme quand le modele se trompe : une preuve
        # absente du log est un signal verifiable, il declenche le re-essai.
        res, calls = self.run_with([diag(0.9, evidence=("ligne inventee",)), diag(0.9)])
        self.assertIn("focus_context", res["path"])
        self.assertEqual(res["retry_reasons"], ["grounding"])
        self.assertEqual(res["grounded"], 1.0)
        self.assertEqual(len(calls), 2)

    def test_category_disagreement_with_rules_triggers_retry(self):
        res, _ = self.run_with([diag(0.9, category="network"), diag(0.9)])
        self.assertEqual(res["retry_reasons"], ["category"])
        self.assertEqual(res["path"].count("diagnose"), 2)

    def test_unknown_rule_category_does_not_trigger_retry(self):
        with mock.patch.object(graph, "rules_classify",
                               return_value={"category": "unknown", "evidence": [], "cause": "", "fix": "",
                                             "confidence": 0.1}):
            res, calls = self.run_with([diag(0.9, category="disk")])
        self.assertNotIn("focus_context", res["path"])
        self.assertEqual(len(calls), 1)

    def test_human_review_interrupt_and_resume(self):
        res, _ = self.run_with([diag(0.9)], review=True, ask=lambda _q: "o")
        self.assertEqual(res["path"][-1], "human_review")
        self.assertEqual(res["review"], "approved")
        res, _ = self.run_with([diag(0.9)], review=True, ask=lambda _q: "n")
        self.assertEqual(res["review"], "rejected")

    def test_mermaid_schema_lists_all_nodes(self):
        text = graph.build(review=True).get_graph().draw_mermaid()
        for node in ("sanitize", "classify", "diagnose", "focus_context", "fallback_rules",
                     "finalize", "human_review"):
            self.assertIn(node, text)


if __name__ == "__main__":
    unittest.main()
