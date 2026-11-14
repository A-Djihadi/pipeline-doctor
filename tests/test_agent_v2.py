"""Tests v2 de l'agent : sources multiples (PD_SOURCES) et piste Jenkins."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from pipeline_doctor import agent, fixflow, journal
from pipeline_doctor.fixflow import FixSettings

try:
    from pipeline_doctor import graph
except ImportError:  # langgraph absent : CI stdlib
    graph = None

FAKE_GITHUB_TOKEN = "ghp_" + "A" * 24
FAKE_JENKINS_TOKEN = "jenkins-api-token"


def _clean_env():
    for name in ("PD_SOURCES", "PD_REPO", "GITHUB_REPOSITORY", "GITHUB_TOKEN",
                 "PD_JENKINS_URL", "PD_JENKINS_JOBS", "PD_JENKINS_USER",
                 "PD_JENKINS_TOKEN", "PD_ENGINE", "PD_FIX_MODE", "PD_FIX_FORK",
                 "PD_FIX_TOKEN", "PD_FIX_MAX_PER_DAY"):
        os.environ.pop(name, None)


class MainSourcesTest(unittest.TestCase):
    def tearDown(self):
        _clean_env()

    def test_v1_default_message_when_unset(self):
        _clean_env()
        err = io.StringIO()
        with redirect_stderr(err):
            rc = agent.main(["--once"])
        self.assertEqual(rc, 2)
        self.assertEqual(err.getvalue(), "Variables manquantes : PD_REPO et GITHUB_TOKEN.\n")

    def test_unset_sources_calls_poll_once_only(self):
        _clean_env()
        os.environ["PD_REPO"] = "o/r"
        os.environ["GITHUB_TOKEN"] = FAKE_GITHUB_TOKEN
        with mock.patch.object(agent, "poll_once", return_value=0) as poll_once, \
                mock.patch.object(agent, "poll_jenkins_once") as poll_jenkins:
            rc = agent.main(["--once"])
        self.assertEqual(rc, 0)
        poll_once.assert_called_once()
        poll_jenkins.assert_not_called()

    def test_jenkins_source_calls_poll_jenkins_once(self):
        _clean_env()
        os.environ["PD_SOURCES"] = "jenkins"
        os.environ["PD_JENKINS_URL"] = "https://jenkins.lan"
        os.environ["PD_JENKINS_JOBS"] = "team/app"
        os.environ["PD_JENKINS_USER"] = "bot"
        os.environ["PD_JENKINS_TOKEN"] = FAKE_JENKINS_TOKEN
        with mock.patch.object(agent, "poll_once") as poll_once, \
                mock.patch.object(agent, "poll_jenkins_once", return_value=0) as poll_jenkins:
            rc = agent.main(["--once"])
        self.assertEqual(rc, 0)
        poll_once.assert_not_called()
        poll_jenkins.assert_called_once()

    def test_jenkins_missing_token_returns_2(self):
        _clean_env()
        os.environ["PD_SOURCES"] = "jenkins"
        os.environ["PD_JENKINS_URL"] = "https://jenkins.lan"
        os.environ["PD_JENKINS_JOBS"] = "team/app"
        os.environ["PD_JENKINS_USER"] = "bot"
        # PD_JENKINS_TOKEN absent
        err = io.StringIO()
        with redirect_stderr(err):
            rc = agent.main(["--once"])
        self.assertEqual(rc, 2)
        self.assertIn("PD_JENKINS_TOKEN", err.getvalue())

    def test_unknown_source_returns_2(self):
        _clean_env()
        os.environ["PD_SOURCES"] = "github,gitlab"
        os.environ["PD_REPO"] = "o/r"
        os.environ["GITHUB_TOKEN"] = FAKE_GITHUB_TOKEN
        err = io.StringIO()
        with redirect_stderr(err):
            rc = agent.main(["--once"])
        self.assertEqual(rc, 2)
        self.assertIn("gitlab", err.getvalue())


class PollJenkinsOnceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "diag.db")
        self.base = "https://jenkins.lan"
        self.jobs = ["team/app"]
        self.webhook = "https://hooks.example/x"

    def tearDown(self):
        self._tmp.cleanup()

    def _diagnosis(self):
        return {"cause": "c", "evidence": ["e"], "fix": "f", "confidence": 0.9}

    def test_sanitizes_and_records(self):
        log = "boom " + FAKE_GITHUB_TOKEN + " fin"
        seen = {}

        def fake_ask(cleaned):
            seen["log"] = cleaned
            return self._diagnosis()

        with mock.patch.object(agent, "list_failed_builds", return_value=[7]), \
                mock.patch.object(agent, "console_log", return_value=log), \
                mock.patch.object(agent, "ask_ollama", side_effect=fake_ask), \
                mock.patch.object(agent, "send") as fake_send:
            n = agent.poll_jenkins_once(self.base, self.jobs, "bot", "tok",
                                        self.db, self.webhook)
        self.assertEqual(n, 1)
        self.assertNotIn(FAKE_GITHUB_TOKEN, seen["log"])
        self.assertIn("[REDACTED", seen["log"])
        fake_send.assert_called_once()
        payload = fake_send.call_args[0][1]
        header = payload["blocks"][0]["text"]["text"]
        self.assertIn("jenkins:team/app", header)
        conn = journal.init(self.db)
        key = agent.build_url(self.base, "team/app", 7)
        row = conn.execute(
            "SELECT status FROM jenkins_diagnostics WHERE build_key=?", (key,)).fetchone()
        self.assertEqual(row[0], "diagnosed")
        conn.close()

    def test_idempotent_second_pass(self):
        with mock.patch.object(agent, "list_failed_builds", return_value=[7]), \
                mock.patch.object(agent, "console_log", return_value="log"), \
                mock.patch.object(agent, "ask_ollama", return_value=self._diagnosis()), \
                mock.patch.object(agent, "send") as fake_send:
            n1 = agent.poll_jenkins_once(self.base, self.jobs, "bot", "tok",
                                         self.db, self.webhook)
            n2 = agent.poll_jenkins_once(self.base, self.jobs, "bot", "tok",
                                         self.db, self.webhook)
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 0)
        fake_send.assert_called_once()

    def test_llm_error_still_notifies(self):
        with mock.patch.object(agent, "list_failed_builds", return_value=[7]), \
                mock.patch.object(agent, "console_log", return_value="log"), \
                mock.patch.object(agent, "ask_ollama", side_effect=RuntimeError("boom")), \
                mock.patch.object(agent, "send") as fake_send:
            n = agent.poll_jenkins_once(self.base, self.jobs, "bot", "tok",
                                        self.db, self.webhook)
        self.assertEqual(n, 1)
        fake_send.assert_called_once()
        payload = fake_send.call_args[0][1]
        self.assertIn("Diagnostic indisponible", json.dumps(payload, ensure_ascii=False))
        conn = journal.init(self.db)
        key = agent.build_url(self.base, "team/app", 7)
        row = conn.execute(
            "SELECT status FROM jenkins_diagnostics WHERE build_key=?", (key,)).fetchone()
        self.assertEqual(row[0], "llm_error")
        conn.close()

    def test_dry_run_prints_and_writes_nothing(self):
        out = io.StringIO()
        with mock.patch.object(agent, "list_failed_builds", return_value=[7]), \
                mock.patch.object(agent, "console_log", return_value="log"), \
                mock.patch.object(agent, "ask_ollama", return_value=self._diagnosis()), \
                mock.patch.object(agent, "send") as fake_send, \
                redirect_stdout(out):
            n = agent.poll_jenkins_once(self.base, self.jobs, "bot", "tok",
                                        self.db, self.webhook, dry_run=True)
        self.assertEqual(n, 1)
        fake_send.assert_not_called()
        payload = json.loads(out.getvalue())
        self.assertIn("jenkins:team/app", payload["blocks"][0]["text"]["text"])
        conn = journal.init(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM jenkins_diagnostics").fetchone()[0], 0)
        conn.close()

    def test_one_job_error_does_not_block_other_jobs(self):
        def fake_list(base, job, user, token, per_job=10):
            if job == "a":
                raise agent.JenkinsError("refused")
            return [7]

        with mock.patch.object(agent, "list_failed_builds", side_effect=fake_list), \
                mock.patch.object(agent, "console_log", return_value="log"), \
                mock.patch.object(agent, "ask_ollama", return_value=self._diagnosis()), \
                mock.patch.object(agent, "send") as fake_send:
            n = agent.poll_jenkins_once(self.base, ["a", "b"], "bot", "tok",
                                        self.db, self.webhook)
        self.assertEqual(n, 1)
        fake_send.assert_called_once()
        conn = journal.init(self.db)
        key = agent.build_url(self.base, "b", 7)
        self.assertTrue(journal.is_build_processed(conn, key))
        conn.close()

    def test_console_error_not_recorded(self):
        def fake_console(base, job, number, user, token,
                         max_tail_bytes=2_097_152, head_bytes=65_536):
            if number == 7:
                raise agent.JenkinsError("boom")
            return "log for 6"

        with mock.patch.object(agent, "list_failed_builds", return_value=[7, 6]), \
                mock.patch.object(agent, "console_log", side_effect=fake_console), \
                mock.patch.object(agent, "ask_ollama", return_value=self._diagnosis()), \
                mock.patch.object(agent, "send") as fake_send:
            n = agent.poll_jenkins_once(self.base, self.jobs, "bot", "tok",
                                        self.db, self.webhook)
        self.assertEqual(n, 1)
        fake_send.assert_called_once()
        conn = journal.init(self.db)
        self.assertFalse(journal.is_build_processed(conn, agent.build_url(self.base, "team/app", 7)))
        self.assertTrue(journal.is_build_processed(conn, agent.build_url(self.base, "team/app", 6)))
        conn.close()


class SourceIsolationTest(unittest.TestCase):
    def tearDown(self):
        _clean_env()

    def test_jenkins_failure_does_not_block_github(self):
        _clean_env()
        os.environ["PD_SOURCES"] = "github,jenkins"
        os.environ["PD_REPO"] = "o/r"
        os.environ["GITHUB_TOKEN"] = FAKE_GITHUB_TOKEN
        os.environ["PD_JENKINS_URL"] = "https://jenkins.lan"
        os.environ["PD_JENKINS_JOBS"] = "team/app"
        os.environ["PD_JENKINS_USER"] = "bot"
        os.environ["PD_JENKINS_TOKEN"] = FAKE_JENKINS_TOKEN
        with mock.patch.object(agent, "poll_once", return_value=0) as poll_once, \
                mock.patch.object(agent, "poll_jenkins_once",
                                  side_effect=RuntimeError("x")) as poll_jenkins:
            rc = agent.main(["--once"])
        self.assertEqual(rc, 0)
        poll_once.assert_called_once()
        poll_jenkins.assert_called_once()


@unittest.skipIf(graph is None, "langgraph non installe")
class JenkinsGraphTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "diag.db")

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("PD_ENGINE", None)

    def test_graph_rules_fallback(self):
        os.environ["PD_ENGINE"] = "graph"
        result = {"diagnosis": {"cause": "c"}, "source": "rules"}
        with mock.patch.object(graph, "run", return_value=result) as run:
            diagnosis, status, source = agent._diagnose(
                "raw", "cleaned", "s", {"source": "jenkins", "job": "a", "build": 1})
        self.assertEqual(status, "rules_fallback")
        self.assertEqual(source, "rules")
        self.assertEqual(diagnosis, {"cause": "c"})
        run.assert_called_once()


class FixModeTest(unittest.TestCase):
    def tearDown(self):
        _clean_env()

    def test_bogus_fix_mode_returns_2(self):
        _clean_env()
        os.environ["PD_REPO"] = "o/r"
        os.environ["GITHUB_TOKEN"] = FAKE_GITHUB_TOKEN
        os.environ["PD_FIX_MODE"] = "bogus"
        err = io.StringIO()
        with redirect_stderr(err):
            rc = agent.main(["--once"])
        self.assertEqual(rc, 2)
        self.assertIn("PD_FIX_MODE", err.getvalue())

    def test_propose_with_jenkins_only_warns_and_never_fixes(self):
        _clean_env()
        os.environ["PD_SOURCES"] = "jenkins"
        os.environ["PD_FIX_MODE"] = "propose"
        os.environ["PD_JENKINS_URL"] = "https://jenkins.lan"
        os.environ["PD_JENKINS_JOBS"] = "team/app"
        os.environ["PD_JENKINS_USER"] = "bot"
        os.environ["PD_JENKINS_TOKEN"] = FAKE_JENKINS_TOKEN
        err = io.StringIO()
        with mock.patch.object(agent, "poll_jenkins_once", return_value=0), \
                mock.patch.object(agent, "run_fix") as run_fix, \
                redirect_stderr(err):
            rc = agent.main(["--once"])
        self.assertEqual(rc, 0)
        run_fix.assert_not_called()
        self.assertIn("PD_FIX_MODE", err.getvalue())

    def test_fix_mode_unset_never_calls_run_fix(self):
        _clean_env()
        os.environ["PD_REPO"] = "o/r"
        os.environ["GITHUB_TOKEN"] = FAKE_GITHUB_TOKEN
        with mock.patch.object(agent, "poll_once", return_value=0) as poll_once, \
                mock.patch.object(agent, "run_fix") as run_fix:
            rc = agent.main(["--once"])
        self.assertEqual(rc, 0)
        poll_once.assert_called_once()
        run_fix.assert_not_called()
        self.assertIsNone(poll_once.call_args.kwargs.get("fix"))

    def test_pr_mode_requires_fork_and_token(self):
        _clean_env()
        os.environ["PD_REPO"] = "o/r"
        os.environ["GITHUB_TOKEN"] = FAKE_GITHUB_TOKEN
        os.environ["PD_FIX_MODE"] = "pr"
        err = io.StringIO()
        with redirect_stderr(err):
            rc = agent.main(["--once"])
        self.assertEqual(rc, 2)
        self.assertIn("PD_FIX_FORK", err.getvalue())

    def test_pr_mode_token_must_differ_from_github_token(self):
        _clean_env()
        os.environ["PD_REPO"] = "o/r"
        os.environ["GITHUB_TOKEN"] = FAKE_GITHUB_TOKEN
        os.environ["PD_FIX_MODE"] = "pr"
        os.environ["PD_FIX_FORK"] = "bot/r"
        os.environ["PD_FIX_TOKEN"] = FAKE_GITHUB_TOKEN
        err = io.StringIO()
        with redirect_stderr(err):
            rc = agent.main(["--once"])
        self.assertEqual(rc, 2)
        self.assertIn("PD_FIX_TOKEN", err.getvalue())
        self.assertNotIn(FAKE_GITHUB_TOKEN, err.getvalue())


class PollOnceFixTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "diag.db")
        self.repo = "A-Djihadi/pipeline-doctor"
        self.token = "t"
        self.failed_run = {"id": 101, "name": "CI", "head_branch": "main", "head_sha": "abc123",
                           "conclusion": "failure",
                           "head_repository": {"full_name": "A-Djihadi/pipeline-doctor"}}
        self.job = {"id": 7, "conclusion": "failure"}

    def tearDown(self):
        self._tmp.cleanup()

    def _diagnosis(self):
        return {"category": "compilation", "cause": "c",
                "evidence": ["[ERROR] App.java:[12,5] cannot find symbol"], "fix": "f",
                "confidence": 0.9}

    def test_fix_slack_message_sent_after_diagnosis(self):
        log = "[ERROR] App.java:[12,5] cannot find symbol\n[ERROR] COMPILATION ERROR"
        settings = FixSettings(mode="propose", read_token="t", fork=None, write_token=None)
        fake_edit = {"edits": [{"path": "src/main/java/App.java", "search": "bar();",
                                "replace": "foo();"}], "summary": "call foo", "confidence": 0.8}
        with mock.patch.object(agent, "list_runs", return_value=[self.failed_run]), \
                mock.patch.object(agent, "list_jobs", return_value=[self.job]), \
                mock.patch.object(agent, "job_log", return_value=log), \
                mock.patch.object(agent, "ask_ollama", return_value=self._diagnosis()), \
                mock.patch.object(agent, "send") as diag_send, \
                mock.patch.object(fixflow.github, "branch_sha", return_value="abc123"), \
                mock.patch.object(fixflow.github, "get_tree",
                                  return_value=({"src/main/java/App.java": "100644"}, False)), \
                mock.patch.object(fixflow.github, "get_file",
                                  return_value="class App {\n  void run() { bar(); }\n}\n"), \
                mock.patch.object(fixflow.github, "open_fix_prs", return_value=[]), \
                mock.patch.object(fixflow, "_default_ask", return_value=fake_edit), \
                mock.patch.object(fixflow, "send") as fix_send:
            n = agent.poll_once(self.repo, self.token, "ci.yml", self.db,
                                "https://hooks/x", fix=settings)
        self.assertEqual(n, 1)
        diag_send.assert_called_once()
        fix_send.assert_called_once()
        self.assertIn("call foo", json.dumps(fix_send.call_args[0][1]))
        conn = journal.init(self.db)
        row = conn.execute("SELECT status, diff FROM fix_proposals").fetchone()
        self.assertEqual(row[0], "proposed")
        self.assertIsNotNone(row[1])
        conn.close()


class EnvExampleTest(unittest.TestCase):
    V2_VARS = ("PD_SOURCES", "PD_JENKINS_URL", "PD_JENKINS_JOBS", "PD_JENKINS_USER",
               "PD_JENKINS_TOKEN", "PD_JENKINS_PER_JOB", "PD_JENKINS_MAX_LOG_BYTES",
               "PD_FIX_MODE", "PD_FIX_FORK", "PD_FIX_TOKEN", "PD_FIX_MAX_PER_DAY")

    def test_all_v2_variables_present(self):
        path = os.path.join(os.path.dirname(__file__), "..", ".env.example")
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        for name in self.V2_VARS:
            self.assertIn(name, content)


if __name__ == "__main__":
    unittest.main()
