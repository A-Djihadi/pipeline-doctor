import unittest

from pipeline_doctor.sanitizer import sanitize, truncate


class SanitizerTest(unittest.TestCase):
    def test_masque_les_secrets(self):
        # Faux secrets assembles a l'execution : aucun litteral ressemblant a une cle
        # dans le depot (evite les faux positifs gitleaks / push protection).
        gh_token = "ghp_" + "a1B2c3D4" * 5
        aws_key = "AKIA" + "ABCDEFGH" * 2
        raw = (
            "DOCKER_PASSWORD=hunter2\n"
            f"Authorization: Bearer {'x' * 8}.{'y' * 16}\n"
            "clone https://user:pass123@github.com/x/y.git\n"
            f"key {aws_key}\n"
            f"GITHUB_TOKEN: {gh_token}\n"
        )
        out, n = sanitize(raw)
        for secret in ("hunter2", "pass123", aws_key, gh_token, "y" * 16):
            self.assertNotIn(secret, out)
        self.assertGreaterEqual(n, 5)

    def test_garde_les_lignes_utiles(self):
        raw = "[ERROR] cannot find symbol\nBUILD FAILURE\n"
        out, n = sanitize(raw)
        self.assertEqual(n, 0)
        self.assertIn("cannot find symbol", out)

    def test_retire_les_codes_ansi(self):
        out, _ = sanitize("\x1b[31mERROR\x1b[0m boom")
        self.assertEqual(out, "ERROR boom")

    def test_troncature_garde_la_fin(self):
        text = "debut\n" + "x" * 50000 + "\nERREUR FINALE"
        out = truncate(text, max_chars=3000, head=500)
        self.assertLessEqual(len(out), 3200)
        self.assertTrue(out.endswith("ERREUR FINALE"))
        self.assertIn("omis", out)


if __name__ == "__main__":
    unittest.main()
