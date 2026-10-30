"""Tests des helpers d'acces a l'API GitHub (redirection des logs)."""
import unittest
from urllib.request import Request

from pipeline_doctor.github import _SafeRedirect


class SafeRedirectTest(unittest.TestCase):
    def test_strips_authorization_on_cross_host_redirect(self):
        h = _SafeRedirect()
        req = Request(
            "https://api.github.com/repos/o/r/actions/jobs/1/logs",
            headers={"Authorization": "Bearer tok", "Accept": "application/vnd.github+json"},
        )
        new = h.redirect_request(
            req, None, 302, "Found",
            {"Location": "https://prod.blob.core.windows.net/x?sig=abc"},
            "https://prod.blob.core.windows.net/x?sig=abc",
        )
        self.assertIsNotNone(new)
        lowered = {k.lower(): v for k, v in new.headers.items()}
        self.assertNotIn("authorization", lowered)
        self.assertIn("accept", lowered)

    def test_keeps_authorization_on_same_host(self):
        h = _SafeRedirect()
        req = Request("https://api.github.com/a", headers={"Authorization": "Bearer tok"})
        new = h.redirect_request(
            req, None, 302, "Found",
            {"Location": "https://api.github.com/b"},
            "https://api.github.com/b",
        )
        self.assertIsNotNone(new)
        lowered = {k.lower(): v for k, v in new.headers.items()}
        self.assertIn("authorization", lowered)


if __name__ == "__main__":
    unittest.main()
