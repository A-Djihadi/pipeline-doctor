"""Tests des helpers de lecture GitHub du flux correctif (GET only)."""
import base64
import io
import json
import unittest
from unittest import mock

from pipeline_doctor import github

API = "https://api.github.com"


class ReadHelpersTest(unittest.TestCase):
    def test_get_tree_blobs_only_and_truncated(self):
        body = json.dumps({
            "tree": [
                {"path": "a.py", "mode": "100644", "type": "blob"},
                {"path": "d", "mode": "040000", "type": "tree"},
            ],
            "truncated": False,
        }).encode()
        with mock.patch.object(github, "_request", return_value=body) as req:
            tree, truncated = github.get_tree("o/r", "sha", "t")
        self.assertEqual(tree, {"a.py": "100644"})
        self.assertFalse(truncated)
        req.assert_called_once_with(f"{API}/repos/o/r/git/trees/sha?recursive=1", "t")

    def test_get_tree_truncated_flag(self):
        body = json.dumps({"tree": [], "truncated": True}).encode()
        with mock.patch.object(github, "_request", return_value=body):
            _, truncated = github.get_tree("o/r", "sha", "t")
        self.assertTrue(truncated)

    def test_get_file_decodes_and_quotes_path(self):
        body = json.dumps({"content": base64.b64encode(b"hello").decode()}).encode()
        with mock.patch.object(github, "_request", return_value=body) as req:
            text = github.get_file("o/r", "src/a b.py", "sha", "t")
        self.assertEqual(text, "hello")
        req.assert_called_once_with(f"{API}/repos/o/r/contents/src/a%20b.py?ref=sha", "t")

    def test_get_file_invalid_utf8_returns_none(self):
        body = json.dumps({"content": base64.b64encode(b"\xff\xfe").decode()}).encode()
        with mock.patch.object(github, "_request", return_value=body):
            self.assertIsNone(github.get_file("o/r", "a.py", "sha", "t"))

    def test_branch_sha(self):
        body = json.dumps({"commit": {"sha": "abc123"}}).encode()
        with mock.patch.object(github, "_request", return_value=body) as req:
            sha = github.branch_sha("o/r", "feature/x", "t")
        self.assertEqual(sha, "abc123")
        req.assert_called_once_with(f"{API}/repos/o/r/branches/feature/x", "t")

    def test_open_fix_prs_filters_by_owner_and_prefix(self):
        prs = [
            {"number": 1, "head": {"ref": "pipeline-doctor/fix-1",
                                   "repo": {"owner": {"login": "bot"}}}},
            {"number": 2, "head": {"ref": "pipeline-doctor/fix-2",
                                   "repo": {"owner": {"login": "other"}}}},
            {"number": 3, "head": {"ref": "feature/x",
                                   "repo": {"owner": {"login": "bot"}}}},
            {"number": 4, "head": {"ref": "pipeline-doctor/fix-4",
                                   "repo": {"owner": {"login": "bot"}}}},
        ]
        body = json.dumps(prs).encode()
        with mock.patch.object(github, "_request", return_value=body):
            numbers = github.open_fix_prs("o/r", "main", "bot", "t")
        self.assertEqual(numbers, [1, 4])


class _RecordingOpener:
    def __init__(self):
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        url = req.full_url
        if "/branches/" in url:
            body = b'{"commit": {"sha": "abc"}}'
        elif "/contents/" in url:
            body = b'{"content": "aGVsbG8="}'
        elif "/git/trees/" in url:
            body = b'{"tree": [], "truncated": false}'
        elif "/pulls" in url:
            body = b'[]'
        elif "/jobs" in url and "/actions/" in url:
            body = b'{"jobs": []}'
        elif "/workflows/" in url:
            body = b'{"workflow_runs": []}'
        elif "/logs" in url:
            body = b'log'
        else:
            body = b'{}'
        return io.BytesIO(body)


class GetOnlySafetyTest(unittest.TestCase):
    def test_every_request_is_get_without_body(self):
        opener = _RecordingOpener()
        with mock.patch.object(github, "_OPENER", opener):
            github.list_jobs("o/r", "1", "t")
            github.list_runs("o/r", "ci.yml", "t")
            github.job_log("o/r", "1", "t")
            github.get_tree("o/r", "sha", "t")
            github.get_file("o/r", "a.py", "sha", "t")
            github.branch_sha("o/r", "main", "t")
            github.open_fix_prs("o/r", "main", "bot", "t")
        self.assertGreaterEqual(len(opener.requests), 7)
        for req in opener.requests:
            self.assertEqual(req.get_method(), "GET", req.full_url)
            self.assertIsNone(req.data)


if __name__ == "__main__":
    unittest.main()
