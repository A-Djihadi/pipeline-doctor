"""Tests du module d'ecriture GitHub : allow-list stricte, draft only, erreurs sans token."""
import inspect
import re
import unittest
import urllib.error
from unittest import mock

from pipeline_doctor import github_write
from pipeline_doctor.github_write import GitHubWriteError

API = "https://api.github.com"

ALLOWED = [
    ("GET", r".*/repos/[^/]+/[^/]+/git/commits/.*"),
    ("POST", r".*/repos/[^/]+/[^/]+/git/blobs$"),
    ("POST", r".*/repos/[^/]+/[^/]+/git/trees$"),
    ("POST", r".*/repos/[^/]+/[^/]+/git/commits$"),
    ("POST", r".*/repos/[^/]+/[^/]+/git/refs$"),
    ("POST", r".*/repos/[^/]+/[^/]+/merge-upstream$"),
    ("POST", r".*/repos/[^/]+/[^/]+/pulls$"),
]


def _resp_for(method, url):
    if method == "GET":
        return {"tree": {"sha": "tree1"}}
    if "/git/blobs" in url:
        return {"sha": "blob1"}
    if "/git/trees" in url:
        return {"sha": "tree2"}
    if "/git/commits" in url:
        return {"sha": "commit1"}
    if "/pulls" in url:
        return {"number": 9, "html_url": "https://github.com/up/r/pull/9", "draft": True}
    return {}


class CommitFilesTest(unittest.TestCase):
    def test_order_and_bodies(self):
        calls = []

        def request(method, url, token, body=None):
            calls.append((method, url, body))
            return _resp_for(method, url)

        with mock.patch.object(github_write, "_request", side_effect=request):
            sha = github_write.commit_files("bot/r", "p", {"a.py": "x"}, "msg", "t")
        self.assertEqual(sha, "commit1")
        self.assertEqual([c[0] for c in calls], ["GET", "POST", "POST", "POST"])
        self.assertEqual(calls[0][1], f"{API}/repos/bot/r/git/commits/p")
        self.assertEqual(calls[1][1], f"{API}/repos/bot/r/git/blobs")
        self.assertEqual(calls[1][2], {"content": "x", "encoding": "utf-8"})
        self.assertEqual(calls[2][1], f"{API}/repos/bot/r/git/trees")
        self.assertEqual(calls[2][2]["base_tree"], "tree1")
        self.assertEqual(calls[2][2]["tree"],
                         [{"path": "a.py", "mode": "100644", "type": "blob", "sha": "blob1"}])
        self.assertEqual(calls[3][1], f"{API}/repos/bot/r/git/commits")
        self.assertEqual(calls[3][2]["parents"], ["p"])


class CreateBranchTest(unittest.TestCase):
    def test_posts_ref_to_fork(self):
        calls = []

        def request(method, url, token, body=None):
            calls.append((method, url, body))
            return {}

        with mock.patch.object(github_write, "_request", side_effect=request):
            github_write.create_branch("bot/r", "pipeline-doctor/fix-1", "s", "t")
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], f"{API}/repos/bot/r/git/refs")
        self.assertEqual(calls[0][2], {"ref": "refs/heads/pipeline-doctor/fix-1", "sha": "s"})

    def test_422_maps_to_branch_exists(self):
        def request(method, url, token, body=None):
            raise GitHubWriteError(422, "github_error:422")

        with mock.patch.object(github_write, "_request", side_effect=request):
            with self.assertRaises(GitHubWriteError) as ctx:
                github_write.create_branch("bot/r", "b", "s", "t")
        self.assertEqual(ctx.exception.reason, "branch_exists")


class OpenDraftPrTest(unittest.TestCase):
    def test_draft_and_maintainer_can_modify(self):
        calls = []

        def request(method, url, token, body=None):
            calls.append((method, url, body))
            return {"number": 9, "html_url": "https://github.com/up/r/pull/9", "draft": True}

        with mock.patch.object(github_write, "_request", side_effect=request):
            number, url = github_write.open_draft_pr("up/r", "bot:ref", "main", "title", "body", "t")
        self.assertEqual((number, url), (9, "https://github.com/up/r/pull/9"))
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], f"{API}/repos/up/r/pulls")
        self.assertTrue(calls[0][2]["draft"])
        self.assertTrue(calls[0][2]["maintainer_can_modify"])

    def test_no_draft_parameter_in_signature(self):
        self.assertNotIn("draft", inspect.signature(github_write.open_draft_pr).parameters)

    def test_refuses_non_draft(self):
        def request(method, url, token, body=None):
            return {"number": 9, "html_url": "x", "draft": False}

        with mock.patch.object(github_write, "_request", side_effect=request):
            with self.assertRaises(GitHubWriteError) as ctx:
                github_write.open_draft_pr("up/r", "bot:ref", "main", "t", "b", "t")
        self.assertEqual(ctx.exception.reason, "draft_refused")


class AllowListTest(unittest.TestCase):
    def _run_all(self, request):
        with mock.patch.object(github_write, "_request", side_effect=request):
            github_write.sync_fork("bot/r", "main", "t")
            github_write.commit_files("bot/r", "p", {"a.py": "x"}, "msg", "t")
            github_write.create_branch("bot/r", "pipeline-doctor/fix-1", "s", "t")
            github_write.open_draft_pr("up/r", "bot:ref", "main", "title", "body", "t")

    def test_every_request_matches_allow_list(self):
        calls = []

        def request(method, url, token, body=None):
            calls.append((method, url))
            return _resp_for(method, url)

        self._run_all(request)
        for method, url in calls:
            ok = any(re.match(m, method) and re.match(p, url) for m, p in ALLOWED)
            self.assertTrue(ok, f"{method} {url}")

    def test_no_write_to_upstream_but_pulls(self):
        calls = []

        def request(method, url, token, body=None):
            calls.append((method, url))
            return _resp_for(method, url)

        self._run_all(request)
        for method, url in calls:
            if method != "GET" and "/repos/up/r/" in url:
                self.assertIn("/pulls", url)

    def test_source_has_no_merge_or_moderation_capability(self):
        with open(github_write.__file__, encoding="utf-8") as fh:
            src = fh.read()
        for i in range(len(src)):
            if src.startswith("/merge", i):
                self.assertTrue(src.startswith("/merge-upstream", i), i)
        for forbidden in ("reviews", "ready_for_review", "markPullRequestReadyForReview",
                          "labels", "comments", "DELETE", "PUT"):
            self.assertNotIn(forbidden, src)

    def test_error_never_leaks_token(self):
        token = "ghp_" + "A" * 24
        err = urllib.error.HTTPError(f"{API}/repos/bot/r/git/refs", 403, "Forbidden", {}, None)
        with mock.patch.object(github_write._OPENER, "open", side_effect=err):
            with self.assertRaises(GitHubWriteError) as ctx:
                github_write._request("POST", f"{API}/repos/bot/r/git/refs", token, {})
        self.assertNotIn(token, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
