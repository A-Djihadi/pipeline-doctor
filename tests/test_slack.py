"""Tests du payload Slack de diagnostic : libelle du fournisseur (provider)."""
import unittest

from pipeline_doctor.slack import build_diagnosis_payload


class DiagnosisPayloadTest(unittest.TestCase):
    def _diagnosis(self):
        return {"cause": "cause x", "evidence": ["e"], "fix": "fix f", "confidence": 0.72}

    def _diagnostic_block(self, payload):
        for block in payload["blocks"]:
            if block.get("type") == "section" and "fields" not in block:
                text = block["text"]["text"]
                if text.startswith("*Diagnostic"):
                    return text
        return None

    def test_default_label_is_ollama(self):
        payload = build_diagnosis_payload(
            "o/r", "CI", "main", "42", "https://example.org/r/42",
            0, self._diagnosis(), "log",
        )
        text = self._diagnostic_block(payload)
        self.assertIn("*Diagnostic (72%)*", text)
        self.assertNotIn("DeepSeek", text)

    def test_deepseek_label(self):
        payload = build_diagnosis_payload(
            "o/r", "CI", "main", "42", "https://example.org/r/42",
            0, self._diagnosis(), "log", provider="deepseek",
        )
        text = self._diagnostic_block(payload)
        self.assertIn("*Diagnostic (via DeepSeek, 72%)*", text)


if __name__ == "__main__":
    unittest.main()
