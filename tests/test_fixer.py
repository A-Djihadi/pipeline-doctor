"""Tests du module pur `fixer` : denylist, candidats, selection, patch, gardes, propose."""
import unittest

from pipeline_doctor import fixer
from pipeline_doctor.fixer import Edit, FixRejected

FAKE_TOKEN = "ghp_" + "A" * 24


class IsDeniedTest(unittest.TestCase):
    def test_denied_paths(self):
        for path in (".github/workflows/ci.yml", "sub/.github/x.yml", "Jenkinsfile",
                     "Jenkinsfile.release", ".env", "config/.env.prod", "certs/server.pem",
                     "app/secrets.py", "package-lock.json", "web/yarn.lock", "CODEOWNERS",
                     ".gitmodules", "../x.py", "/etc/passwd", "JENKINSFILE"):
            self.assertTrue(fixer.is_denied(path), path)

    def test_allowed_paths(self):
        for path in ("src/app.py", "pom.xml", "package.json", "Dockerfile"):
            self.assertFalse(fixer.is_denied(path), path)


class CandidatePathsTest(unittest.TestCase):
    def test_runner_path_resolves_to_tree_path(self):
        log = "/home/runner/work/x/x/src/main/java/App.java:[12,5] cannot find symbol"
        tree = {"src/main/java/App.java": "100644"}
        self.assertEqual(fixer.candidate_paths(log, tree, "compilation"),
                         ["src/main/java/App.java"])

    def test_windows_path_resolves(self):
        log = "D:\\a\\x\\x\\src\\app.py:3 something"
        tree = {"src/app.py": "100644"}
        self.assertEqual(fixer.candidate_paths(log, tree, "compilation"), ["src/app.py"])

    def test_ambiguous_suffix_ignored(self):
        tree = {"a/util.py": "100644", "b/util.py": "100644"}
        self.assertEqual(fixer.candidate_paths("ERROR util.py boom", tree, "compilation"), [])

    def test_dependency_manifests_exclude_lockfile(self):
        tree = {"package.json": "100644", "package-lock.json": "100644", "src/app.js": "100644"}
        self.assertEqual(fixer.candidate_paths("no paths here", tree, "dependency"),
                         ["package.json"])

    def test_drops_executable_and_denied_and_caps(self):
        tree = {"tool.py": "100755", ".github/workflows/ci.yml": "100644",
                "a.py": "100644", "b.py": "100644", "c.py": "100644", "d.py": "100644"}
        log = "tool.py .github/workflows/ci.yml a.py b.py c.py d.py"
        out = fixer.candidate_paths(log, tree, "compilation")
        self.assertNotIn("tool.py", out)
        self.assertNotIn(".github/workflows/ci.yml", out)
        self.assertEqual(out, ["a.py", "b.py", "c.py"])  # ordre + cap MAX_FILES


class SelectFilesTest(unittest.TestCase):
    def test_drops_missing_and_oversized(self):
        self.assertEqual(fixer.select_files({"a.py": "ok", "b.py": None,
                                             "c.py": "x" * 20001}), {"a.py": "ok"})

    def test_secret_in_source(self):
        with self.assertRaises(FixRejected) as ctx:
            fixer.select_files({"a.py": "token = " + FAKE_TOKEN})
        self.assertEqual((ctx.exception.status, ctx.exception.reason),
                         ("skipped", "secret_in_source"))

    def test_empty(self):
        with self.assertRaises(FixRejected) as ctx:
            fixer.select_files({})
        self.assertEqual((ctx.exception.status, ctx.exception.reason),
                         ("skipped", "no_candidate_files"))


class ParseEditsTest(unittest.TestCase):
    def test_valid(self):
        edits, summary, confidence = fixer.parse_edits(
            {"edits": [{"path": "a", "search": "x", "replace": "y"}],
             "summary": "s\nt", "confidence": 2})
        self.assertEqual(edits, [Edit("a", "x", "y")])
        self.assertEqual(summary, "s t")
        self.assertEqual(confidence, 1.0)

    def test_no_edits(self):
        with self.assertRaises(FixRejected) as ctx:
            fixer.parse_edits({"edits": []})
        self.assertEqual((ctx.exception.status, ctx.exception.reason), ("no_fix", "no_edits"))

    def test_bad_json(self):
        for bad in ({"edits": "x"}, {"edits": [{"path": "a", "search": "", "replace": "y"}]}):
            with self.assertRaises(FixRejected) as ctx:
                fixer.parse_edits(bad)
            self.assertEqual((ctx.exception.status, ctx.exception.reason), ("no_fix", "bad_json"))


class ApplyEditsTest(unittest.TestCase):
    def test_single_occurrence(self):
        out = fixer.apply_edits({"a.py": "def f():\n    bar()\n"},
                                [Edit("a.py", "bar()", "foo()")])
        self.assertEqual(out, {"a.py": "def f():\n    foo()\n"})

    def test_unknown_path(self):
        with self.assertRaises(FixRejected) as ctx:
            fixer.apply_edits({"a.py": "x"}, [Edit("b.py", "x", "y")])
        self.assertEqual((ctx.exception.status, ctx.exception.reason),
                         ("rejected", "unknown_path"))

    def test_search_not_found(self):
        with self.assertRaises(FixRejected) as ctx:
            fixer.apply_edits({"a.py": "abc"}, [Edit("a.py", "zzz", "y")])
        self.assertEqual(ctx.exception.reason, "search_not_found")

    def test_search_not_unique(self):
        with self.assertRaises(FixRejected) as ctx:
            fixer.apply_edits({"a.py": "x x"}, [Edit("a.py", "x", "y")])
        self.assertEqual(ctx.exception.reason, "search_not_unique")

    def test_no_change(self):
        with self.assertRaises(FixRejected) as ctx:
            fixer.apply_edits({"a.py": "x"}, [Edit("a.py", "x", "x")])
        self.assertEqual(ctx.exception.reason, "no_change")

    def test_crlf_preserved_no_final_newline(self):
        out = fixer.apply_edits({"a.py": "foo\r\nbar"}, [Edit("a.py", "foo\nbar", "foo\nbaz")])
        self.assertEqual(out, {"a.py": "foo\r\nbaz"})

    def test_crlf_final_newline_preserved(self):
        out = fixer.apply_edits({"a.py": "foo\r\nbar\r\n"}, [Edit("a.py", "bar", "baz")])
        self.assertEqual(out, {"a.py": "foo\r\nbaz\r\n"})


class RenderDiffTest(unittest.TestCase):
    def test_headers_and_changed_only(self):
        diff = fixer.render_diff({"a.py": "x\n", "b.py": "same\n"},
                                 {"a.py": "y\n", "b.py": "same\n"})
        self.assertIn("--- a/a.py", diff)
        self.assertIn("+++ b/a.py", diff)
        self.assertNotIn("b/b.py", diff)


class GuardTest(unittest.TestCase):
    def _guard(self, original, patched):
        fixer.guard(original, patched, fixer.render_diff(original, patched))

    def test_too_many_files(self):
        with self.assertRaises(FixRejected) as ctx:
            self._guard({"a.py": "1", "b.py": "1", "c.py": "1", "d.py": "1"},
                        {"a.py": "2", "b.py": "2", "c.py": "2", "d.py": "2"})
        self.assertEqual(ctx.exception.reason, "too_many_files")

    def test_too_many_lines(self):
        original = {"a.py": "\n".join(str(i) for i in range(50)) + "\n"}
        patched = {"a.py": "\n".join(str(i) + "!" for i in range(50)) + "\n"}
        with self.assertRaises(FixRejected) as ctx:
            self._guard(original, patched)
        self.assertEqual(ctx.exception.reason, "too_many_lines")

    def test_denied_path(self):
        with self.assertRaises(FixRejected) as ctx:
            self._guard({".github/workflows/ci.yml": "a"}, {".github/workflows/ci.yml": "b"})
        self.assertEqual(ctx.exception.reason, "denied_path")

    def test_secret_token(self):
        original = {"a.py": "x = 1\n"}
        patched = {"a.py": "x = 1\ntoken = " + FAKE_TOKEN + "\n"}
        with self.assertRaises(FixRejected) as ctx:
            self._guard(original, patched)
        self.assertEqual(ctx.exception.reason, "secret_in_patch")

    def test_secret_kv(self):
        original = {"a.py": "x\n"}
        patched = {"a.py": "x\npassword = hunter2\n"}
        with self.assertRaises(FixRejected) as ctx:
            self._guard(original, patched)
        self.assertEqual(ctx.exception.reason, "secret_in_patch")

    def test_redacted_placeholder(self):
        original = {"a.py": "x\n"}
        patched = {"a.py": "x\n[REDACTED:secret]\n"}
        with self.assertRaises(FixRejected) as ctx:
            self._guard(original, patched)
        self.assertEqual(ctx.exception.reason, "redacted_placeholder")

    def test_unsafe_fix(self):
        original = {"a.py": "run\n"}
        patched = {"a.py": "run\nmvn -DskipTests\n"}
        with self.assertRaises(FixRejected) as ctx:
            self._guard(original, patched)
        self.assertEqual(ctx.exception.reason, "unsafe_fix")

    def test_unsafe_on_removed_line_is_ok(self):
        # Le meme motif sur une ligne SUPPRIMEE ne declenche pas unsafe_fix.
        self._guard({"a.py": "mvn -DskipTests\n"}, {"a.py": ""})

    def test_clean_fix_passes(self):
        self._guard({"a.py": "def f():\n    return bar\n"},
                    {"a.py": "def f():\n    return foo\n"})


class EligibilityTest(unittest.TestCase):
    def test_not_llm(self):
        self.assertEqual(fixer.eligibility(None, "log", source="llm"), "not_llm")
        diag = {"category": "compilation", "confidence": 0.9}
        self.assertEqual(fixer.eligibility(diag, "log", source="rules"), "not_llm")

    def test_non_fixable_category(self):
        self.assertEqual(fixer.eligibility({"category": "oom", "confidence": 0.9}, "log",
                                           source="llm"), "category:oom")
        self.assertEqual(fixer.eligibility({"category": "config", "confidence": 0.9}, "log",
                                           source="llm"), "category:config")

    def test_low_confidence(self):
        diag = {"category": "compilation", "confidence": 0.5, "evidence": ["COMPILATION ERROR"]}
        self.assertEqual(fixer.eligibility(diag, "COMPILATION ERROR", source="llm"),
                         "low_confidence")

    def test_ungrounded(self):
        diag = {"category": "compilation", "confidence": 0.9, "evidence": ["not in log"]}
        self.assertEqual(fixer.eligibility(diag, "COMPILATION ERROR", source="llm"), "ungrounded")

    def test_category_disagreement(self):
        log = "Tests run: 1, Failures: 1"
        diag = {"category": "compilation", "confidence": 0.9,
                "evidence": ["Tests run: 1, Failures: 1"]}
        self.assertEqual(fixer.eligibility(diag, log, source="llm"), "category_disagreement")

    def test_unknown_rules_category_ok(self):
        log = "nothing recognizable"
        diag = {"category": "compilation", "confidence": 0.9,
                "evidence": ["nothing recognizable"]}
        self.assertIsNone(fixer.eligibility(diag, log, source="llm"))

    def test_grounded_compilation(self):
        log = "COMPILATION ERROR :\nsomething"
        diag = {"category": "compilation", "confidence": 0.9,
                "evidence": ["COMPILATION ERROR :"]}
        self.assertIsNone(fixer.eligibility(diag, log, source="llm"))


class BuildPromptTest(unittest.TestCase):
    def test_contains_diagnostic_and_files(self):
        diag = {"category": "compilation", "cause": "missing method", "fix": "rename"}
        prompt = fixer.build_prompt(diag, "COMPILATION ERROR\nboom", {"a.py": "line1\r\nline2"})
        self.assertIn("category: compilation", prompt)
        self.assertIn("cause: missing method", prompt)
        self.assertIn("=== a.py", prompt)
        self.assertIn("line1\nline2", prompt)
        self.assertNotIn("\r\n", prompt)

    def test_error_windows_capped_at_4000(self):
        log = "\n".join("ERROR line %d" % i for i in range(2000))
        diag = {"category": "compilation", "cause": "c", "fix": "f"}
        prompt = fixer.build_prompt(diag, log, {})
        section = prompt.split("Error lines (sanitized):", 1)[1].lstrip("\n")
        self.assertLessEqual(len(section), 4000)


class FixSystemPromptTest(unittest.TestCase):
    def test_contract_keywords(self):
        for kw in ("JSON", "edits", "search", "replace", "--force"):
            self.assertIn(kw, fixer.FIX_SYSTEM_PROMPT)
        self.assertIn("tests", fixer.FIX_SYSTEM_PROMPT.lower())


class ProposeTest(unittest.TestCase):
    DIAG = {"category": "compilation", "cause": "c", "fix": "f", "confidence": 0.9}

    def test_propose_end_to_end(self):
        seen = {}

        def ask(system, user):
            seen["system"] = system
            seen["user"] = user
            return {"edits": [{"path": "a.py", "search": "bar()", "replace": "foo()"}],
                    "summary": "call foo", "confidence": 0.8}

        prop = fixer.propose(self.DIAG, "COMPILATION ERROR", {"a.py": "def f():\n    bar()\n"}, ask)
        self.assertEqual(prop.files, ("a.py",))
        self.assertIn("foo()", prop.patched["a.py"])
        self.assertIn("--- a/a.py", prop.diff)
        self.assertIn("+++ b/a.py", prop.diff)
        self.assertEqual(prop.summary, "call foo")
        self.assertEqual(prop.confidence, 0.8)
        self.assertEqual(seen["system"], fixer.FIX_SYSTEM_PROMPT)
        self.assertIn("=== a.py", seen["user"])

    def test_ask_error_maps_to_llm_error(self):
        def ask(system, user):
            raise RuntimeError("down")

        with self.assertRaises(FixRejected) as ctx:
            fixer.propose(self.DIAG, "log", {"a.py": "x"}, ask)
        self.assertEqual((ctx.exception.status, ctx.exception.reason), ("no_fix", "llm_error"))

    def test_non_dict_maps_to_bad_json(self):
        with self.assertRaises(FixRejected) as ctx:
            fixer.propose(self.DIAG, "log", {"a.py": "x"}, lambda s, u: "not a dict")
        self.assertEqual((ctx.exception.status, ctx.exception.reason), ("no_fix", "bad_json"))

    def test_bypass_edit_rejected(self):
        def ask(system, user):
            return {"edits": [{"path": "a.py", "search": "run", "replace": "npm install --force"}],
                    "summary": "s", "confidence": 0.8}

        with self.assertRaises(FixRejected) as ctx:
            fixer.propose(self.DIAG, "log", {"a.py": "run\n"}, ask)
        self.assertEqual((ctx.exception.status, ctx.exception.reason), ("rejected", "unsafe_fix"))


if __name__ == "__main__":
    unittest.main()
