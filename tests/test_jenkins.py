"""Tests du client Jenkins read-only (stdlib uniquement)."""
import base64
import io
import json
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request

from pipeline_doctor import github, jenkins


def b64(user, token):
    return base64.b64encode(f"{user}:{token}".encode()).decode()


class UrlTest(unittest.TestCase):
    def test_job_url_nested(self):
        self.assertEqual(jenkins.job_url("https://j", "team/app/main"),
                         "https://j/job/team/job/app/job/main")

    def test_job_url_quotes_segment(self):
        self.assertEqual(jenkins.job_url("https://j/", "a b"), "https://j/job/a%20b")

    def test_job_url_does_not_double_encode(self):
        self.assertEqual(jenkins.job_url("https://j", "feature%2Fx"),
                         "https://j/job/feature%2Fx")

    def test_build_url(self):
        self.assertEqual(jenkins.build_url("https://j", "app", 12), "https://j/job/app/12/")


class CheckBaseUrlTest(unittest.TestCase):
    def test_http_non_loopback_rejected(self):
        with self.assertRaises(jenkins.JenkinsError):
            jenkins.check_base_url("http://jenkins.lan")

    def test_loopback_http_accepted(self):
        self.assertEqual(jenkins.check_base_url("http://localhost:8080"),
                         "http://localhost:8080")
        self.assertEqual(jenkins.check_base_url("http://127.0.0.1"), "http://127.0.0.1")

    def test_https_accepted_and_slash_stripped(self):
        self.assertEqual(jenkins.check_base_url("https://jenkins.lan/"), "https://jenkins.lan")

    def test_userinfo_rejected(self):
        with self.assertRaises(jenkins.JenkinsError):
            jenkins.check_base_url("https://u:***@jenkins.lan")


class ListFailedBuildsTest(unittest.TestCase):
    def _open_returning(self, payload):
        resp = io.BytesIO(json.dumps(payload).encode())

        def open_(req, timeout=None):
            self.req = req
            return resp

        return open_

    def test_url_and_filter_newest_failure(self):
        payload = {"builds": [
            {"number": 5, "result": "FAILURE", "building": False},
            {"number": 6, "result": "SUCCESS", "building": False},
            {"number": 7, "result": "FAILURE", "building": True},
            {"number": 8, "result": None, "building": False},
            {"number": 4, "result": "FAILURE", "building": False},
        ]}
        with mock.patch.object(jenkins._OPENER, "open", self._open_returning(payload)):
            out = jenkins.list_failed_builds("https://j", "app", "u", "tok")
        self.assertEqual(out, [5, 4])
        self.assertEqual(
            self.req.full_url,
            "https://j/job/app/api/json?tree=builds[number,result,building]",
        )

    def test_per_job_examines_only_newest(self):
        payload = {"builds": [
            {"number": 5, "result": "FAILURE", "building": False},
            {"number": 6, "result": "FAILURE", "building": False},
            {"number": 7, "result": "FAILURE", "building": False},
            {"number": 8, "result": "FAILURE", "building": False},
            {"number": 4, "result": "FAILURE", "building": False},
        ]}
        with mock.patch.object(jenkins._OPENER, "open", self._open_returning(payload)):
            out = jenkins.list_failed_builds("https://j", "app", "u", "tok", per_job=2)
        self.assertEqual(out, [8, 7])

    def test_get_no_body_and_auth_headers(self):
        payload = {"builds": []}
        with mock.patch.object(jenkins._OPENER, "open", self._open_returning(payload)):
            jenkins.list_failed_builds("https://j", "app", "u", "tok")
        self.assertEqual(self.req.get_method(), "GET")
        self.assertIsNone(self.req.data)
        self.assertEqual(self.req.get_header("Authorization"), "Basic " + b64("u", "tok"))
        self.assertEqual(self.req.get_header("User-agent"), "pipeline-doctor")
        self.assertEqual(self.req.get_header("Accept"), "application/json")


class ConsoleLogTest(unittest.TestCase):
    class ChunkedStream:
        def __init__(self, data):
            self._io = io.BytesIO(data)
            self.max_read = 0

        def read(self, n=-1):
            self.max_read = max(self.max_read, n)
            return self._io.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_bounded_head_tail_and_marker(self):
        body = b"x" * (10 * 1024 * 1024)
        stream = self.ChunkedStream(body)

        def open_(req, timeout=None):
            self.req = req
            return stream

        with mock.patch.object(jenkins._OPENER, "open", side_effect=open_):
            out = jenkins.console_log("https://j", "app", 7, "u", "tok",
                                      max_tail_bytes=1024, head_bytes=512)
        self.assertEqual(self.req.full_url, "https://j/job/app/7/consoleText")
        self.assertEqual(self.req.get_header("Accept"), "text/plain")
        self.assertEqual(out[:512], "x" * 512)
        self.assertIn("omitted", out)
        self.assertTrue(out.endswith("x" * 1024))
        self.assertEqual(stream.max_read, 65536)  # lecture par chunks


class StripConsoleNotesTest(unittest.TestCase):
    def test_strips_note(self):
        self.assertEqual(jenkins.strip_console_notes("a\x1b[8mha:AAAA==\x1b[0mb"), "ab")

    def test_console_log_has_no_payload(self):
        body = b"[INFO] start\n\x1b[8mha:AAAA==\x1b[0m[ERROR] fail\n"

        def open_(req, timeout=None):
            return io.BytesIO(body)

        with mock.patch.object(jenkins._OPENER, "open", side_effect=open_):
            out = jenkins.console_log("https://j", "app", 1, "u", "tok",
                                      max_tail_bytes=1024, head_bytes=512)
        self.assertNotIn("ha:", out)


class ErrorTest(unittest.TestCase):
    def test_403_raises_jenkins_error_without_token(self):
        token = "supersecrettoken"

        def open_(req, timeout=None):
            raise HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b""))

        with mock.patch.object(jenkins._OPENER, "open", side_effect=open_):
            with self.assertRaises(jenkins.JenkinsError) as ctx:
                jenkins.list_failed_builds("https://j", "app", "u", token)
        self.assertIn("PD_JENKINS_USER", str(ctx.exception))
        self.assertNotIn(token, str(ctx.exception))


class SafeRedirectTest(unittest.TestCase):
    def test_imported_not_duplicated(self):
        self.assertIs(jenkins._SafeRedirect, github._SafeRedirect)

    def test_cross_host_redirect_drops_authorization(self):
        h = jenkins._SafeRedirect()
        req = Request("https://jenkins.lan/job/app/api/json",
                      headers={"Authorization": "Basic x"})
        new = h.redirect_request(
            req, None, 302, "Found",
            {"Location": "https://other.host/x"},
            "https://other.host/x",
        )
        self.assertIsNotNone(new)
        lowered = {k.lower(): v for k, v in new.headers.items()}
        self.assertNotIn("authorization", lowered)


if __name__ == "__main__":
    unittest.main()
