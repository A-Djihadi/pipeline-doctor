# Tickets — v2 (Jenkins source + fix proposals as draft PRs)

Read first: ADR-0007 (`docs/adr/0007-jenkins-ingestion-poll.md`), ADR-0008
(`docs/adr/0008-fix-pr-human-validation.md`), ADR-0009
(`docs/adr/0009-v2-backward-compatibility.md`), then the spec
(`docs/superpowers/specs/pipeline-doctor-v2.md`). Every ticket is **test-first**: the acceptance
criteria below are the tests to write first (RED), then make green. Complexity S/M/L.
All tests run with:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Standard library only — no new dependency. **Global criterion for every ticket:** the v1 test
files `tests/test_agent.py`, `test_notify.py`, `test_sanitizer.py`, `test_journal.py`,
`test_github.py` pass **unmodified** (ADR-0009). Fake secrets are built at runtime
(`"ghp_" + "A" * 24`), never literal.

---

## V1 — Additive journal tables (S)

**What it delivers:** `jenkins_diagnostics` and `fix_proposals` tables plus accessors, created by
`journal.init()` without touching `diagnostics`.

**Blocked by:** None.

**RED acceptance criteria (tests in `tests/test_journal_v2.py`):**

- [ ] `journal.init(":memory:")` creates tables `diagnostics`, `jenkins_diagnostics`,
      `fix_proposals` (query `sqlite_master`).
- [ ] A file DB created with the **v1 schema only** (create `diagnostics` by hand, insert a row)
      then opened with `journal.init(path)` keeps the row intact and gains the two new tables;
      the `diagnostics` column list (`PRAGMA table_info`) is unchanged.
- [ ] `is_build_processed(conn, key)` is `False`, then `True` after
      `record_build(conn, key, "https://j", "team/app", 7, {"cause": "c", "evidence": ["e"]}, "diagnosed")`.
- [ ] `record_build(..., None, "llm_error")` stores `NULL` cause/confidence/fix and `"[]"` evidence.
- [ ] `record_fix(conn, row)` twice with the same `(source, source_ref)` leaves one row with the
      second status.
- [ ] `fixes_since(conn, repo, since)` counts only `proposed` and `pr_opened` rows of that repo
      created at or after `since` (seed `skipped`, `rejected`, other repo, old rows).

---

## V2 — Jenkins read client `jenkins.py` (M)

**What it delivers:** URL building, failed-build listing, bounded console download and
ConsoleNote stripping, GET-only, credentials safe.

**Blocked by:** None.

**RED acceptance criteria (tests in `tests/test_jenkins.py`, patching `jenkins._request` / the opener):**

- [ ] `job_url("https://j", "team/app/main") == "https://j/job/team/job/app/job/main"`;
      `job_url("https://j/", "a b")` quotes the segment (`a%20b`); a pre-encoded `feature%2Fx`
      segment is not double-encoded.
- [ ] `build_url("https://j", "app", 12) == "https://j/job/app/12/"`.
- [ ] `check_base_url("http://jenkins.lan")` raises `JenkinsError`; `http://localhost:8080`,
      `http://127.0.0.1` and `https://jenkins.lan` are accepted (trailing `/` stripped); a URL
      with userinfo (`https://u:p@jenkins.lan`) is rejected.
- [ ] `list_failed_builds` requests exactly
      `{job_url}/api/json?tree=builds[number,result,building]` (brackets may be percent-encoded)
      and, for a response with builds `{5 FAILURE}`, `{6 SUCCESS}`, `{7 FAILURE building}`,
      `{8 null}`, `{4 FAILURE}`, returns `[5, 4]` (newest first, only finished `FAILURE`).
- [ ] `per_job=2` on 5 builds examines only the 2 highest numbers, whatever the response order.
- [ ] `console_log` requests `{build_url}consoleText`; on a 10 MiB fake body with
      `max_tail_bytes=1024, head_bytes=512` it returns the first 512 bytes, an omission marker
      containing `omitted`, and the last 1024 bytes (memory: the fake stream is read in chunks).
- [ ] `strip_console_notes("a\x1b[8mha:AAAA==\x1b[0mb") == "ab"`; `console_log` output contains
      no `ha:` payload from a body that had one.
- [ ] Every request is `GET` with no body, carries `Authorization: Basic <b64(user:token)>` and
      `User-Agent: pipeline-doctor`.
- [ ] Cross-host redirect drops `Authorization` (same assertion style as `tests/test_github.py`).
- [ ] An HTTP 403 raises `JenkinsError` whose message contains `PD_JENKINS_USER` and does **not**
      contain the token value.

---

## V3 — Agent multi-source: `PD_SOURCES` + `poll_jenkins_once` (M)

**What it delivers:** the agent polls Jenkins jobs through the same diagnosis core, with
per-source isolation and v1-identical defaults.

**Blocked by:** V1, V2.

**RED acceptance criteria (tests in `tests/test_agent_v2.py`, patching names on `agent`):**

- [ ] With `PD_SOURCES` unset, `agent.main(["--once"])` without `PD_REPO`/`GITHUB_TOKEN` returns
      2 and prints exactly the v1 message; with them set it calls `poll_once` once and never
      `poll_jenkins_once`.
- [ ] `PD_SOURCES=jenkins` with the four `PD_JENKINS_*` variables and **no** `PD_REPO`/`GITHUB_TOKEN`
      calls `poll_jenkins_once` and returns 0.
- [ ] `PD_SOURCES=jenkins` missing `PD_JENKINS_TOKEN` returns 2, stderr names `PD_JENKINS_TOKEN`.
- [ ] `PD_SOURCES=github,gitlab` returns 2 (unknown source).
- [ ] `poll_jenkins_once` on one failed build: the text passed to `ask_ollama` is sanitized (a fake
      GitHub token in the console is absent, `[REDACTED` present), Slack `send` receives a payload
      whose header contains `jenkins:team/app`, and `jenkins_diagnostics` has one `diagnosed` row
      keyed by the build URL.
- [ ] Second `poll_jenkins_once` pass on the same build: no LLM call, no Slack call (idempotent).
- [ ] `ask_ollama` raising → row status `llm_error` and Slack still called with "Diagnostic indisponible".
- [ ] `--dry-run`: payload printed, no Slack call, no row written.
- [ ] `list_failed_builds` raising `JenkinsError` for job A does not prevent job B from being
      processed in the same pass; with `PD_SOURCES=github,jenkins`, a Jenkins failure does not
      prevent `poll_once` from running.
- [ ] `console_log` raising for one build → that build is **not** recorded (retried next pass).
- [ ] `PD_ENGINE=graph` path for Jenkins is covered in `tests/test_agent_v2.py` behind the same
      `skipUnless(langgraph available)` guard as `tests/test_graph.py`, asserting status
      `rules_fallback` when the graph reports `source == "rules"`.

---

## V4 — Shared stdlib helpers: `grounding`, `UNSAFE_FIX`, `llm.chat(user_template=)` (S)

**What it delivers:** the helpers the fix logic needs, available without the optional extra.

**Blocked by:** None.

**RED acceptance criteria (tests in `tests/test_rules.py` additions + `tests/test_llm.py`):**

- [ ] `from pipeline_doctor.rules import grounding, UNSAFE_FIX` works in the stdlib-only job.
- [ ] `rules.grounding({"evidence": ["a", "zz"]}, "a b") == 0.5`; no evidence → `0.0`.
- [ ] `rules.UNSAFE_FIX.search("mvn -DskipTests")` matches; `"mvn verify"` does not.
- [ ] `graph.grounding is rules.grounding` and `evals.UNSAFE_FIX is rules.UNSAFE_FIX` (tests guarded
      by the existing langgraph skip) — existing `tests/test_graph.py` / `test_evals.py` unchanged.
- [ ] `llm.chat(system, "X")` (patched `urlopen`) sends a user message equal to
      `"Log du build en echec :\n\nX"` (v1 default); `llm.chat(system, "X", user_template="{log}")`
      sends exactly `"X"`.

---

## V5 — `fixer`: path policy, candidate files, file selection (M)

**What it delivers:** the deterministic choice of which files the model may see and edit.

**Blocked by:** None.

**RED acceptance criteria (tests in `tests/test_fixer.py`):**

- [ ] `is_denied` is true for `.github/workflows/ci.yml`, `sub/.github/x.yml`, `Jenkinsfile`,
      `Jenkinsfile.release`, `.env`, `config/.env.prod`, `certs/server.pem`, `app/secrets.py`,
      `package-lock.json`, `web/yarn.lock`, `CODEOWNERS`, `.gitmodules`, `../x.py`, `/etc/passwd`,
      and case variants (`JENKINSFILE`); false for `src/app.py`, `pom.xml`, `package.json`,
      `Dockerfile`.
- [ ] Log line `/home/runner/work/x/x/src/main/java/App.java:[12,5] cannot find symbol` with tree
      `{"src/main/java/App.java": "100644"}` → `["src/main/java/App.java"]`.
- [ ] Windows path `D:\a\x\x\src\app.py:3` maps to `src/app.py`.
- [ ] Ambiguous suffix (`a/util.py` and `b/util.py` in tree, log mentions `util.py`) → not selected.
- [ ] Category `dependency` with tree containing `package.json` and `package-lock.json` and a log
      without paths → `["package.json"]` (lockfile denied).
- [ ] Executable file (mode `100755`) and denied paths are never returned; at most 3 paths; order
      = first seen in error windows, then rest of the log, then manifests.
- [ ] `select_files({"a.py": "ok", "b.py": None, "c.py": "x" * 20001})` → `{"a.py": "ok"}`.
- [ ] `select_files({"a.py": "token = " + fake})` raises `FixRejected` with
      `status == "skipped"` and `reason == "secret_in_source"`; `select_files({})` →
      `reason == "no_candidate_files"`.

---

## V6 — `fixer`: parse, apply, diff, guards (M)

**What it delivers:** exact-match patch application and every output guard, failing closed.

**Blocked by:** V4, V5.

**RED acceptance criteria (tests in `tests/test_fixer.py`):**

- [ ] `parse_edits({"edits": [{"path": "a", "search": "x", "replace": "y"}], "summary": "s\nt", "confidence": 2})`
      → one `Edit`, summary `"s t"`, confidence `1.0`.
- [ ] `parse_edits({"edits": []})` → `FixRejected("no_fix", "no_edits")`; `{"edits": "x"}` or an
      edit with empty `search` → `("no_fix", "bad_json")`.
- [ ] `apply_edits` replaces the single occurrence; unknown path → `rejected:unknown_path`; zero
      occurrences → `search_not_found`; two occurrences → `search_not_unique`; identical result →
      `no_change`.
- [ ] CRLF file: search written with `\n` matches, patched text keeps `\r\n`; missing final newline
      is preserved.
- [ ] `render_diff` produces `--- a/<path>` / `+++ b/<path>` headers and only changed files.
- [ ] `guard` raises, with the listed reason: 4 files → `too_many_files`; 41 changed lines →
      `too_many_lines`; patched path `.github/workflows/ci.yml` → `denied_path`; added
      `"ghp_" + "A"*24` → `secret_in_patch`; added `password = hunter2` (kv pattern) →
      `secret_in_patch`; `[REDACTED:secret]` in patched text → `redacted_placeholder`; an added
      line containing `-DskipTests`, `@pytest.mark.skip` or `npm install --force` → `unsafe_fix`;
      the same pattern on a *removed* line does not trigger `unsafe_fix`.
- [ ] A clean one-line fix passes `guard` without exception.

---

## V7 — `fixer`: eligibility, prompt, `propose` (M)

**What it delivers:** the gate that decides whether a diagnosis may produce a patch, the model
contract, and the end-to-end pure pipeline with an injected LLM.

**Blocked by:** V4, V6.

**RED acceptance criteria (tests in `tests/test_fixer.py`):**

- [ ] `eligibility` returns, in order of precedence: `not_llm` (diagnosis `None` or
      `source="rules"`), `category:oom` (non-fixable), `category:config`, `low_confidence` (0.5),
      `ungrounded` (an evidence line absent from the log), `category_disagreement` (rules say
      `tests`, model says `compilation`); `None` for a grounded compilation diagnosis at 0.9.
- [ ] `rules` category `unknown` does not cause `category_disagreement`.
- [ ] `build_prompt` contains the category, the cause, each file path after `=== `, file text
      with LF endings, no line numbers, and the error windows capped at 4000 chars.
- [ ] `FIX_SYSTEM_PROMPT` mentions JSON-only output, `edits`, `search`, `replace`, empty `edits`
      allowed, and forbids `--force` and disabling tests (keyword assertions).
- [ ] `propose(diag, log, files, ask=fake)` with a fake returning a valid edit → `Proposal` with
      `files`, `patched`, `diff`, `summary`, `confidence`; the fake received the system prompt and
      the built user message.
- [ ] `ask` raising → `FixRejected("no_fix", "llm_error")`; returning a non-dict → `bad_json`.
- [ ] A fake returning a bypass edit → `FixRejected("rejected", "unsafe_fix")` (guards run inside
      `propose`).

---

## V8 — `github.py` read helpers (S)

**What it delivers:** tree, file, branch tip and open-fix-PR lookups, still GET-only.

**Blocked by:** None.

**RED acceptance criteria (tests in `tests/test_github_read.py`, patching `github._request`):**

- [ ] `get_tree("o/r", "sha", t)` requests `/repos/o/r/git/trees/sha?recursive=1` and returns
      `({"a.py": "100644"}, False)` keeping only `type == "blob"` entries; `truncated: true` is
      returned as `True`.
- [ ] `get_file` requests `/repos/o/r/contents/src/a%20b.py?ref=sha` and base64-decodes; invalid
      UTF-8 → `None`.
- [ ] `branch_sha("o/r", "feature/x", t)` requests `/repos/o/r/branches/feature/x` (path quoted
      with `safe="/"`) and returns `commit.sha`.
- [ ] `open_fix_prs` keeps only PRs whose head repo owner is the fork owner and head ref starts
      with `pipeline-doctor/fix-`.
- [ ] Safety: every `urllib.request.Request` built by `github.py` (all functions, v1 included)
      has `get_method() == "GET"` and `data is None` (patch the opener, call each function).

---

## V9 — `github_write.py`: fork commit + draft PR (M)

**What it delivers:** the only write path, minimal and allow-listed.

**Blocked by:** None.

**RED acceptance criteria (tests in `tests/test_github_write.py`, patching `github_write._request`):**

- [ ] `commit_files("bot/r", "p", {"a.py": "x"}, "msg", t)` issues, in order: `GET
      /repos/bot/r/git/commits/p`, `POST .../git/blobs`, `POST .../git/trees` with `base_tree` =
      the parent's tree and entry mode `100644`, `POST .../git/commits` with `parents == ["p"]`;
      returns the new commit sha.
- [ ] `create_branch` posts `{"ref": "refs/heads/pipeline-doctor/fix-1", "sha": ...}` to the
      **fork**; a 422 raises `GitHubWriteError` with `reason == "branch_exists"`.
- [ ] `open_draft_pr` posts to `/repos/up/r/pulls` with `"draft": true` and
      `"maintainer_can_modify": true`; the function has no parameter controlling `draft`
      (`inspect.signature`).
- [ ] A response with `"draft": false` raises `GitHubWriteError(reason="draft_refused")`.
- [ ] Allow-list: across all functions, every request matches one of
      `GET /repos/{fork}/git/commits/*`, `POST /repos/{fork}/git/(blobs|trees|commits|refs)`,
      `POST /repos/{fork}/merge-upstream`, `POST /repos/{upstream}/pulls` — and no write targets
      the upstream repo other than `POST /pulls`.
- [ ] Source scan of `github_write.py`: no `"/merge"` except `merge-upstream`, no `reviews`,
      `ready_for_review`, `markPullRequestReadyForReview`, `labels`, `comments`, `DELETE`, `PUT`.
- [ ] Error messages never contain the token value.

---

## V10 — `fixflow` + Slack fix payload + `propose` mode in the agent (L)

**What it delivers:** end-to-end `PD_FIX_MODE=propose`: eligible run → guarded diff in Slack and
journal, every other outcome journaled with its reason.

**Blocked by:** V1, V3, V7, V8.

**RED acceptance criteria (tests in `tests/test_fixflow.py` and `tests/test_agent_v2.py`):**

- [ ] `PD_FIX_MODE=bogus` → `agent.main` returns 2; `PD_FIX_MODE=propose` with
      `PD_SOURCES=jenkins` → warning on stderr, fix flow never called.
- [ ] `PD_FIX_MODE` unset → `poll_once` is called with `fix=None` and `fixflow.run_fix` is never
      called (patch it and assert not called).
- [ ] Eligible run (fakes for `github.branch_sha`, `get_tree`, `get_file`, `ask`):
      `fix_proposals` row `status="proposed"`, `reason is None`, `diff` non-empty, `files` JSON;
      `slack.send` called twice (diagnosis, then fix payload containing the diff).
- [ ] Each run-level skip is journaled with no LLM call and no fix Slack message:
      `fork_run` (`head_repository.full_name != repo`), `own_branch`
      (`head_branch="pipeline-doctor/fix-3"`), `stale` (tip ≠ `head_sha`), `daily_limit` (3 seeded
      `proposed` rows in the last 24 h), `tree_truncated`.
- [ ] Diagnosis-level skip (e.g. `category:oom`) is journaled `skipped` without any GitHub read.
- [ ] Guard rejection (fake LLM adds `--force`) → row `rejected/unsafe_fix`, `diff` is `NULL`, only
      the diagnosis Slack message is sent.
- [ ] `run_fix` turning an unexpected exception into row `error/exception:<Class>`; the
      `diagnostics` row and its status are unchanged.
- [ ] `--dry-run` with `propose`: fix payload printed, no `fix_proposals` row, no Slack call.
- [ ] `build_fix_payload` header contains `Correctif propose`, the diff appears inside a code
      block, total text per block ≤ 3000 chars, PR section present only when `pr_url` is given.
- [ ] No `github_write` function is ever called in `propose` mode (patch module, assert no calls).

---

## V11 — `pr` mode: fork sync, commit, draft PR (M)

**What it delivers:** end-to-end `PD_FIX_MODE=pr` on top of V10.

**Blocked by:** V9, V10.

**RED acceptance criteria (tests in `tests/test_fixflow.py`, `tests/test_agent_v2.py`):**

- [ ] `PD_FIX_MODE=pr` without `PD_FIX_TOKEN` or `PD_FIX_FORK` → exit 2; with
      `PD_FIX_TOKEN == GITHUB_TOKEN` → exit 2 and stderr mentions `PD_FIX_TOKEN` (no value).
- [ ] Eligible run: calls in order `open_fix_prs` (upstream, read token) → `sync_fork(fork,
      head_branch)` → `branch_sha(fork, head_branch)` → `commit_files(fork, head_sha, patched, msg)`
      → `create_branch(fork, "pipeline-doctor/fix-<run_id>", sha)` → `open_draft_pr(repo,
      "<fork_owner>:pipeline-doctor/fix-<run_id>", head_branch, title, body)`; every call on the
      **fork** (read or write) and `open_draft_pr` use `PD_FIX_TOKEN`; every read on the
      **upstream** repo uses `GITHUB_TOKEN`.
- [ ] Row `pr_opened` with `pr_number`, `pr_url`, `head_branch`; fix Slack payload contains the
      PR URL.
- [ ] Existing open fix PR on that base → `skipped/open_pr_exists`, no write call.
- [ ] Fork tip ≠ `head_sha` after sync → `error/fork_out_of_sync`, no commit/branch/PR call.
- [ ] `create_branch` → `branch_exists` and `open_draft_pr` → `draft_refused` map to `error`
      rows with those reasons; no retry within the pass.
- [ ] Title and commit message match the spec templates exactly and contain no model text (a
      model `summary` with a backtick fence and `@someone` appears only inside a fenced block of
      the body whose fence is longer than any backtick run in it).
- [ ] Body contains the warning block, run URL, short SHA, evidence lines and validation steps.

---

## V12 — Documentation and operations (S)

**What it delivers:** user-facing docs and owner runbook for v2.

**Blocked by:** V3, V11.

**RED acceptance criteria (docs review + one test):**

- [ ] `.env.example` lists every new variable from the spec (commented, no value for secrets);
      a test asserts each name from the spec's configuration table appears in `.env.example`.
- [ ] README: "Principes" updated (read-only towards CI and monitored repo; opt-in draft PRs from
      a bot fork, never merged by the agent — ADR-0008); new sections "Jenkins" and "Correctifs
      proposes (PR)"; the `curl …/consoleText | python -m pipeline_doctor -` alternative; roadmap
      v2 ticked.
- [ ] `deploy/README.md`: Jenkins read-only user + token; `Contents: Read-only` for propose; the
      `pr` owner actions 3–8 from the spec, verbatim order.
- [ ] `CHANGELOG.md` entry; `docs/README.md` links ADR-0007/0008/0009.

---

## Order and edges

```
V1 ───────────────┐
V2 ── V3 ─────────┼──────────── V10 ── V11 ── V12
V4 ──┬── V6 ── V7 ┤               │      │
V5 ──┘            │               │      │
V8 ───────────────┘               │      │
V9 ───────────────────────────────┴──────┘
```

Edges: V3 ← V1, V2 · V6 ← V4, V5 · V7 ← V4, V6 · V10 ← V1, V3, V7, V8 · V11 ← V9, V10 ·
V12 ← V3, V11. Unblocked at start: **V1, V2, V4, V5, V8, V9** (parallelisable). The Jenkins
track (V1–V3) is shippable on its own before any fix work lands.
