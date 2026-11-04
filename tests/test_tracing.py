"""Tests Langfuse : structure des traces (spans par noeud, generation, scores) et masquage.

Aucun serveur : les spans OpenTelemetry sont captures en memoire.
"""
import os
import unittest
from unittest import mock

try:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from pipeline_doctor import graph, tracing
except ImportError:
    graph = None

FAKE_TOKEN = "ghp_" + "C" * 24


@unittest.skipIf(graph is None, "extra agent (langgraph, langfuse) non installe")
class TracingTest(unittest.TestCase):
    # Le SDK Langfuse garde un singleton par cle publique : un seul client pour la classe.
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test",
            "LANGFUSE_BASE_URL": "http://127.0.0.1:9", "LANGFUSE_DISABLED": ""})
        cls.env.start()
        tracing.reset()
        cls.exporter = InMemorySpanExporter()
        tracing.client(span_exporter=cls.exporter)

    @classmethod
    def tearDownClass(cls):
        tracing.reset()
        cls.env.stop()

    def setUp(self):
        self.exporter.clear()

    def test_trace_has_node_spans_and_generation(self):
        meta = {"model": "qwen2.5-coder:3b", "model_parameters": {"temperature": 0.1},
                "usage": {"input": 120, "output": 40}}
        d = {"cause": "c", "evidence": ["[ERROR] COMPILATION ERROR :"], "fix": "f", "confidence": 0.9}
        with mock.patch.object(graph, "chat", return_value=(d, meta)), \
                mock.patch.object(tracing.client(), "get_prompt", side_effect=RuntimeError("offline")):
            graph.run("[ERROR] COMPILATION ERROR :\n")
        tracing.flush()
        spans = {s.name: s for s in self.exporter.get_finished_spans()}
        for name in ("pipeline-doctor.diagnose", "sanitize", "classify", "diagnose", "finalize", "ollama-chat"):
            self.assertIn(name, spans)
        gen = spans["ollama-chat"]
        self.assertEqual(gen.attributes.get("langfuse.observation.type"), "generation")
        self.assertEqual(gen.attributes.get("langfuse.observation.model.name"), "qwen2.5-coder:3b")
        # la generation est rattachee au noeud diagnose
        self.assertEqual(gen.parent.span_id, spans["diagnose"].context.span_id)

    def test_raw_log_is_never_traced(self):
        # Le log brut ne doit pas transiter par l'etat du graphe : sinon chaque span de noeud
        # l'exporte en entree, et seul `mask` empeche la fuite.
        d = {"cause": "c", "evidence": [], "fix": "f", "confidence": 0.9}
        meta = {"model": "m", "model_parameters": {}, "usage": {"input": 1, "output": 1}}
        with mock.patch.object(graph, "chat", return_value=(d, meta)), \
                mock.patch.object(tracing.client(), "get_prompt", side_effect=RuntimeError("offline")):
            graph.run("[ERROR] boom\nAuthorization: Bearer " + FAKE_TOKEN + "\n", review=True, ask=lambda _q: "o")
        tracing.flush()
        spans = self.exporter.get_finished_spans()
        self.assertTrue(spans)
        for s in spans:
            for value in s.attributes.values():
                self.assertNotIn("raw_log", str(value), s.name)
                self.assertNotIn(FAKE_TOKEN, str(value), s.name)

    def test_mask_redacts_nested_data(self):
        masked = tracing.mask(data={"a": ["token " + FAKE_TOKEN], "b": 3})
        self.assertNotIn(FAKE_TOKEN, str(masked))
        self.assertEqual(masked["b"], 3)


if __name__ == "__main__":
    unittest.main()
