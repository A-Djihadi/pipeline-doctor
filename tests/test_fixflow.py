"""Tests de l'orchestration du correctif : run_fix (propose + pr) et payload Slack."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from pipeline_doctor import fixflow, journal
from pipeline_doctor.fixflow import FixSettings
from pipeline_doctor.slack import build_fix_payload

REPO = "A-Djihadi/pipeline-doctor"
RUN = {"id": 101, "name": "CI", "head_branch": "main", "head_sha": "abc123",
       "conclusion": "failure", "head_repository": {"full_name": REPO}}
DIAG = {"category": "compilation", "cause": "Missing method foo()",
        "evidence": ["[ERROR] App.java:[12,5] cannot find symbol"], "fix": "Rename bar to foo",
        "confidence": 0.9}
LOG = "[ERROR] App.java:[12,5] cannot find symbol\n[ERROR] COMPILATION ERROR"
TREE = {"src/main/java/App.java": "100644"}
FILES = {"src/main/java/App.java": "class App {\n  void run() { bar(); }\n}\n"}
WEBHOOK = "https://hooks.example/x"


def fake_ask_ok(system, user):
    return {"edits": [{"path": "src/main/java/App.java", "search": "bar();", "replace": "foo();"}],
            "summary": "call foo", "confidence": 0.8}


def _settings(mode="propose", fork=None, write_token=None, read_token="readtok"):
    return FixSettings(mode=mode, read_token=read_token, fork=fork, write_token=write_token)


def _run_fix(conn, run=None, diag=DIAG, source="llm", cleaned=LOG, settings=None, ask=None,
             branch_sha="abc123", tree=TREE, truncated=False, files=FILES, open_prs=None):
    settings = settings or _settings()
    ask = ask or fake_ask_ok
    send_mock = mock.MagicMock()
    with mock.patch.object(fixflow.github, "branch_sha", return_value=branch_sha), \
            mock.patch.object(fixflow.github, "get_tree", return_value=(tree, truncated)), \
            mock.patch.object(fixflow.github, "get_file",
                              side_effect=lambda r, p, s, t: files.get(p)), \
            mock.patch.object(fixflow.github, "open_fix_prs", return_value=open_prs or []), \
            mock.patch.object(fixflow, "send", send_mock):
        row = fixflow.run_fix(conn, REPO, run or dict(RUN), diag, source, cleaned,
                              settings, WEBHOOK, False, ask=ask)
    return row, send_mock, ask


class ProposeModeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = journal.init(os.path.join(self._tmp.name, "d.db"))

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_eligible_run_proposes(self):
        row, send, _ = _run_fix(self.conn)
        self.assertEqual(row["status"], "proposed")
        self.assertIsNone(row["reason"])
        self.assertIsNotNone(row["diff"])
        self.assertEqual(row["files"], ["src/main/java/App.java"])
        send.assert_called_once()
        db = self.conn.execute("SELECT status, reason, files, diff FROM fix_proposals").fetchone()
        self.assertEqual(db[0], "proposed")
        self.assertIsNone(db[1])
        self.assertEqual(json.loads(db[2]), ["src/main/java/App.java"])
        self.assertIsNotNone(db[3])

    def test_fork_run_skip(self):
        run = dict(RUN, head_repository={"full_name": "other/repo"})
        row, send, ask = _run_fix(self.conn, run=run, ask=mock.MagicMock())
        self.assertEqual((row["status"], row["reason"]), ("skipped", "fork_run"))
        ask.assert_not_called()
        send.assert_not_called()

    def test_own_branch_skip(self):
        run = dict(RUN, head_branch="pipeline-doctor/fix-3")
        row, send, ask = _run_fix(self.conn, run=run, ask=mock.MagicMock())
        self.assertEqual((row["status"], row["reason"]), ("skipped", "own_branch"))
        ask.assert_not_called()
        send.assert_not_called()

    def test_stale_skip(self):
        row, send, ask = _run_fix(self.conn, branch_sha="different", ask=mock.MagicMock())
        self.assertEqual((row["status"], row["reason"]), ("skipped", "stale"))
        ask.assert_not_called()
        send.assert_not_called()

    def test_daily_limit_skip(self):
        for i in range(3):
            journal.record_fix(self.conn, {"source": "github", "source_ref": str(i),
                                           "repo": REPO, "status": "proposed"})
        row, send, ask = _run_fix(self.conn, ask=mock.MagicMock())
        self.assertEqual((row["status"], row["reason"]), ("skipped", "daily_limit"))
        ask.assert_not_called()
        send.assert_not_called()

    def test_tree_truncated_skip(self):
        row, send, ask = _run_fix(self.conn, truncated=True, ask=mock.MagicMock())
        self.assertEqual((row["status"], row["reason"]), ("skipped", "tree_truncated"))
        ask.assert_not_called()
        send.assert_not_called()

    def test_diagnosis_level_skip_no_github_read(self):
        diag = {"category": "oom", "confidence": 0.9, "evidence": []}
        with mock.patch.object(fixflow.github, "branch_sha") as branch_sha, \
                mock.patch.object(fixflow.github, "get_tree") as get_tree, \
                mock.patch.object(fixflow, "send") as send:
            row = fixflow.run_fix(self.conn, REPO, dict(RUN), diag, "llm", LOG,
                                  _settings(), WEBHOOK, False, ask=mock.MagicMock())
        self.assertEqual((row["status"], row["reason"]), ("skipped", "category:oom"))
        branch_sha.assert_not_called()
        get_tree.assert_not_called()
        send.assert_not_called()

    def test_guard_rejection_unsafe_fix(self):
        def ask(system, user):
            return {"edits": [{"path": "src/main/java/App.java", "search": "bar();",
                               "replace": "mvn -DskipTests"}], "summary": "s", "confidence": 0.8}

        row, send, _ = _run_fix(self.conn, ask=ask)
        self.assertEqual((row["status"], row["reason"]), ("rejected", "unsafe_fix"))
        send.assert_not_called()
        db = self.conn.execute("SELECT diff FROM fix_proposals").fetchone()
        self.assertIsNone(db[0])

    def test_unexpected_exception_maps_to_error(self):
        with mock.patch.object(fixflow.github, "branch_sha", return_value="abc123"), \
                mock.patch.object(fixflow.github, "get_tree", side_effect=ValueError("boom")), \
                mock.patch.object(fixflow, "send") as send:
            row = fixflow.run_fix(self.conn, REPO, dict(RUN), DIAG, "llm", LOG,
                                  _settings(), WEBHOOK, False, ask=mock.MagicMock())
        self.assertEqual((row["status"], row["reason"]), ("error", "exception:ValueError"))
        send.assert_not_called()

    def test_dry_run_prints_and_writes_nothing(self):
        out = io.StringIO()
        with mock.patch.object(fixflow.github, "branch_sha", return_value="abc123"), \
                mock.patch.object(fixflow.github, "get_tree", return_value=(TREE, False)), \
                mock.patch.object(fixflow.github, "get_file",
                                  side_effect=lambda r, p, s, t: FILES.get(p)), \
                mock.patch.object(fixflow, "send") as send, \
                redirect_stdout(out):
            row = fixflow.run_fix(self.conn, REPO, dict(RUN), DIAG, "llm", LOG,
                                  _settings(), WEBHOOK, True, ask=fake_ask_ok)
        self.assertEqual(row["status"], "proposed")
        send.assert_not_called()
        payload = json.loads(out.getvalue())
        self.assertIn("Correctif propose", payload["blocks"][0]["text"]["text"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fix_proposals").fetchone()[0], 0)

    def test_no_github_write_in_propose_mode(self):
        with mock.patch.object(fixflow.github, "branch_sha", return_value="abc123"), \
                mock.patch.object(fixflow.github, "get_tree", return_value=(TREE, False)), \
                mock.patch.object(fixflow.github, "get_file",
                                  side_effect=lambda r, p, s, t: FILES.get(p)), \
                mock.patch.object(fixflow, "send"), \
                mock.patch.object(fixflow.github_write, "sync_fork") as sync_fork, \
                mock.patch.object(fixflow.github_write, "commit_files") as commit_files, \
                mock.patch.object(fixflow.github_write, "create_branch") as create_branch, \
                mock.patch.object(fixflow.github_write, "open_draft_pr") as open_draft_pr:
            fixflow.run_fix(self.conn, REPO, dict(RUN), DIAG, "llm", LOG,
                            _settings(), WEBHOOK, False, ask=fake_ask_ok)
        sync_fork.assert_not_called()
        commit_files.assert_not_called()
        create_branch.assert_not_called()
        open_draft_pr.assert_not_called()


class PrModeTest(unittest.TestCase):
    PR = FixSettings(mode="pr", read_token="readtok", fork="bot/r", write_token="writetok")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = journal.init(os.path.join(self._tmp.name, "d.db"))

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_pr_flow_order_and_row(self):
        order = []

        def step(name, ret=None):
            def fn(*args, **kwargs):
                order.append(name)
                return ret
            return fn

        with mock.patch.object(fixflow.github, "branch_sha",
                               side_effect=["abc123", "abc123"]), \
                mock.patch.object(fixflow.github, "get_tree", return_value=(TREE, False)), \
                mock.patch.object(fixflow.github, "get_file",
                                  side_effect=lambda r, p, s, t: FILES.get(p)), \
                mock.patch.object(fixflow.github, "open_fix_prs",
                                  side_effect=step("open_fix_prs", [])), \
                mock.patch.object(fixflow.github_write, "sync_fork",
                                  side_effect=step("sync_fork")), \
                mock.patch.object(fixflow.github_write, "commit_files",
                                  side_effect=step("commit_files", "newsha")), \
                mock.patch.object(fixflow.github_write, "create_branch",
                                  side_effect=step("create_branch")), \
                mock.patch.object(fixflow.github_write, "open_draft_pr",
                                  side_effect=step("open_draft_pr", (9, "https://github.com/A-Djihadi/pipeline-doctor/pull/9"))), \
                mock.patch.object(fixflow, "send") as send:
            row = fixflow.run_fix(self.conn, REPO, dict(RUN), DIAG, "llm", LOG,
                                  self.PR, WEBHOOK, False, ask=fake_ask_ok)
        self.assertEqual(order, ["open_fix_prs", "sync_fork", "commit_files",
                                 "create_branch", "open_draft_pr"])
        self.assertEqual(row["status"], "pr_opened")
        self.assertEqual(row["pr_number"], 9)
        self.assertEqual(row["pr_url"], "https://github.com/A-Djihadi/pipeline-doctor/pull/9")
        self.assertEqual(row["head_branch"], "pipeline-doctor/fix-101")
        send.assert_called_once()
        self.assertIn("pull/9", json.dumps(send.call_args[0][1]))

    def test_token_separation(self):
        branch_calls = []
        sync_calls = []

        def branch(repo, branch, token):
            branch_calls.append((repo, token))
            return "abc123"

        def sync(fork, branch, token):
            sync_calls.append((fork, token))

        with mock.patch.object(fixflow.github, "branch_sha", side_effect=branch), \
                mock.patch.object(fixflow.github, "get_tree", return_value=(TREE, False)), \
                mock.patch.object(fixflow.github, "get_file",
                                  side_effect=lambda r, p, s, t: FILES.get(p)), \
                mock.patch.object(fixflow.github, "open_fix_prs", return_value=[]), \
                mock.patch.object(fixflow.github_write, "sync_fork", side_effect=sync), \
                mock.patch.object(fixflow.github_write, "commit_files", return_value="newsha"), \
                mock.patch.object(fixflow.github_write, "create_branch"), \
                mock.patch.object(fixflow.github_write, "open_draft_pr",
                                  return_value=(9, "url")), \
                mock.patch.object(fixflow, "send"):
            fixflow.run_fix(self.conn, REPO, dict(RUN), DIAG, "llm", LOG,
                            self.PR, WEBHOOK, False, ask=fake_ask_ok)
        self.assertEqual(branch_calls, [(REPO, "readtok"), ("bot/r", "writetok")])
        self.assertEqual(sync_calls, [("bot/r", "writetok")])

    def test_open_pr_exists_skip(self):
        with mock.patch.object(fixflow.github, "branch_sha", return_value="abc123"), \
                mock.patch.object(fixflow.github, "open_fix_prs", return_value=[7]), \
                mock.patch.object(fixflow.github_write, "sync_fork") as sync_fork, \
                mock.patch.object(fixflow, "send") as send:
            row = fixflow.run_fix(self.conn, REPO, dict(RUN), DIAG, "llm", LOG,
                                  self.PR, WEBHOOK, False, ask=mock.MagicMock())
        self.assertEqual((row["status"], row["reason"]), ("skipped", "open_pr_exists"))
        sync_fork.assert_not_called()
        send.assert_not_called()

    def test_fork_out_of_sync(self):
        with mock.patch.object(fixflow.github, "branch_sha",
                               side_effect=["abc123", "different"]), \
                mock.patch.object(fixflow.github, "get_tree", return_value=(TREE, False)), \
                mock.patch.object(fixflow.github, "get_file",
                                  side_effect=lambda r, p, s, t: FILES.get(p)), \
                mock.patch.object(fixflow.github, "open_fix_prs", return_value=[]), \
                mock.patch.object(fixflow.github_write, "sync_fork"), \
                mock.patch.object(fixflow.github_write, "commit_files") as commit_files, \
                mock.patch.object(fixflow, "send"):
            row = fixflow.run_fix(self.conn, REPO, dict(RUN), DIAG, "llm", LOG,
                                  self.PR, WEBHOOK, False, ask=fake_ask_ok)
        self.assertEqual((row["status"], row["reason"]), ("error", "fork_out_of_sync"))
        commit_files.assert_not_called()

    def test_branch_exists_maps_to_error(self):
        with mock.patch.object(fixflow.github, "branch_sha",
                               side_effect=["abc123", "abc123"]), \
                mock.patch.object(fixflow.github, "get_tree", return_value=(TREE, False)), \
                mock.patch.object(fixflow.github, "get_file",
                                  side_effect=lambda r, p, s, t: FILES.get(p)), \
                mock.patch.object(fixflow.github, "open_fix_prs", return_value=[]), \
                mock.patch.object(fixflow.github_write, "sync_fork"), \
                mock.patch.object(fixflow.github_write, "commit_files", return_value="newsha"), \
                mock.patch.object(fixflow.github_write, "create_branch",
                                  side_effect=fixflow.github_write.GitHubWriteError(422, "branch_exists")), \
                mock.patch.object(fixflow, "send") as send:
            row = fixflow.run_fix(self.conn, REPO, dict(RUN), DIAG, "llm", LOG,
                                  self.PR, WEBHOOK, False, ask=fake_ask_ok)
        self.assertEqual((row["status"], row["reason"]), ("error", "branch_exists"))
        send.assert_not_called()

    def test_draft_refused_maps_to_error(self):
        with mock.patch.object(fixflow.github, "branch_sha",
                               side_effect=["abc123", "abc123"]), \
                mock.patch.object(fixflow.github, "get_tree", return_value=(TREE, False)), \
                mock.patch.object(fixflow.github, "get_file",
                                  side_effect=lambda r, p, s, t: FILES.get(p)), \
                mock.patch.object(fixflow.github, "open_fix_prs", return_value=[]), \
                mock.patch.object(fixflow.github_write, "sync_fork"), \
                mock.patch.object(fixflow.github_write, "commit_files", return_value="newsha"), \
                mock.patch.object(fixflow.github_write, "create_branch"), \
                mock.patch.object(fixflow.github_write, "open_draft_pr",
                                  side_effect=fixflow.github_write.GitHubWriteError(422, "draft_refused")), \
                mock.patch.object(fixflow, "send") as send:
            row = fixflow.run_fix(self.conn, REPO, dict(RUN), DIAG, "llm", LOG,
                                  self.PR, WEBHOOK, False, ask=fake_ask_ok)
        self.assertEqual((row["status"], row["reason"]), ("error", "draft_refused"))
        send.assert_not_called()


class TemplatesTest(unittest.TestCase):
    def test_commit_and_title_exact(self):
        model = fixflow.llm.OLLAMA_MODEL
        run = {"id": 101, "head_branch": "main"}
        diag = {"category": "compilation"}
        self.assertEqual(
            fixflow._commit_message(diag, run, model),
            "fix: compilation failure in run 101 (pipeline-doctor)\n\n"
            f"Proposed by pipeline-doctor with local model {model}. "
            "Human review required before merge.")
        self.assertEqual(fixflow._pr_title(diag, run),
                         "fix(pipeline-doctor): compilation failure on main (run 101)")

    def test_fence_wraps_backtick_runs(self):
        fenced = fixflow._fence("```\nfoo @someone\n```")
        lines = fenced.splitlines()
        self.assertEqual(lines[0], lines[-1])
        self.assertEqual(len(lines[0]), 4)

    def test_pr_body_contains_required_sections_and_fences_model_text(self):
        class P:
            summary = "sum ``` @someone"

        run = {"id": 101, "head_branch": "main", "head_sha": "abc123"}
        diag = {"category": "compilation", "cause": "cause x", "evidence": ["ev1"]}
        body = fixflow._pr_body(diag, P(), run, REPO, "qwen2.5-coder:3b")
        self.assertIn("[!WARNING]", body)
        self.assertIn("actions/runs/101", body)
        self.assertIn("abc123", body)
        self.assertIn("ev1", body)
        self.assertIn("How to validate", body)
        self.assertIn("````\nsum ``` @someone\n````", body)


class BuildFixPayloadTest(unittest.TestCase):
    def test_header_diff_fence_and_pr_section(self):
        payload = build_fix_payload("o/r", 1, "https://github.com/o/r/actions/runs/1",
                                    "proposed", None, "summary", "diff line")
        self.assertIn("Correctif propose", payload["blocks"][0]["text"]["text"])
        texts = [b["text"]["text"] for b in payload["blocks"]
                 if b["type"] == "section" and "text" in b]
        self.assertTrue(any("```diff line```" in t for t in texts))
        self.assertNotIn("Pull request", json.dumps(payload))

        with_pr = build_fix_payload("o/r", 1, "u", "pr_opened", None, "s", "d",
                                    pr_url="https://github.com/o/r/pull/1")
        self.assertIn("Pull request", json.dumps(with_pr))

    def test_diff_truncated_to_block_limit(self):
        payload = build_fix_payload("o/r", 1, "u", "proposed", None, "s", "x" * 5000)
        for block in payload["blocks"]:
            if "text" in block and block["text"].get("text"):
                self.assertLessEqual(len(block["text"]["text"]), 3000)


if __name__ == "__main__":
    unittest.main()
