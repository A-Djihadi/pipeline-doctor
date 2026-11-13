# Coder brief — v2 (Jenkins source + fix proposals as draft PRs)

Implement the design from ADR-0007, ADR-0008, ADR-0009 and the spec. Two independent tracks:
a **Jenkins source** (read-only, like the GitHub poller) and **fix proposals** (opt-in, the first
feature that can produce code). Everything is **off by default**: with no new variable set, the
project must behave exactly like v1.

Read first (in order): `docs/adr/0009-v2-backward-compatibility.md`,
`docs/adr/0007-jenkins-ingestion-poll.md`, `docs/adr/0008-fix-pr-human-validation.md`, then
`docs/superpowers/specs/pipeline-doctor-v2.md`, then `docs/tickets-v2.md`.

## Ground rules

- **Zero dependency** (ADR-0001): `urllib`, `json`, `base64`, `sqlite3`, `difflib`, `fnmatch`,
  `re`, `dataclasses`, `inspect` only. No `pip install`. Nothing new imports `langgraph` or
  `langfuse` at module level (the stdlib-only CI job must stay green).
- **Ollama only** (ADR-0004): fix generation goes through `llm.chat`; no other model, no cloud key.
- **Secrets**: the sanitizer runs before anything reaches the model; a repository file in which
  `redact()` finds anything is never sent nor edited; a patch with any `redact()` match is
  rejected. Never log a token, not even partially. Fake secrets in tests are built at runtime.
- **Backward compatible** (ADR-0009): do not modify `cli.py`, `notify.py`, `sanitizer.py`,
  `slack.build_payload`, `slack.build_diagnosis_payload`, `.github/workflows/*`, or the
  `diagnostics` table. Do not edit `tests/test_agent.py`, `test_notify.py`, `test_sanitizer.py`,
  `test_journal.py`, `test_github.py` — they are the characterization suite and must stay green.
- **Fail closed**: every uncertain situation in the fix flow ends in a journaled
  `skipped`/`rejected`/`error` row, never in a write.
- **Write isolation**: only `github_write.py` may issue a non-`GET` request to GitHub; only
  `POST /pulls` may target the upstream repo; `draft` is always `true`. No merge, review,
  ready-for-review, label, comment, `PUT` or `DELETE` anywhere.
- **Test-first**: write each ticket's RED tests from `docs/tickets-v2.md`, then implement. Keep
  `PYTHONPATH=src python -m unittest discover -s tests -v` green after every commit, with and
  without `requirements-agent.txt`.

## Implementation order

Start with the unblocked tickets (V1, V2, V4, V5, V8, V9 — any order, parallelisable), then
V3 → V6 → V7 → V10 → V11 → V12. Commit per ticket, English messages
(e.g. `feat: jenkins read-only client`, `feat: fixer guards`). The Jenkins track (V1–V3) can be
opened as its own PR before the fix track.

### V1 — `src/pipeline_doctor/journal.py` (extend)

- In `init()`, after the existing `CREATE TABLE IF NOT EXISTS diagnostics`, add the two
  `CREATE TABLE IF NOT EXISTS` statements from the spec, verbatim. No `ALTER`, no migration.
- Add `is_build_processed`, `record_build` (mirror `record`, same JSON evidence encoding, same
  `_now()`), `record_fix(conn, row)` (`INSERT OR REPLACE`, sets `created_at`; `files` JSON-encoded
  if a list), `fixes_since(conn, repo, since_iso)`.
- Leave `init`/`is_processed`/`record` behaviour identical.

### V2 — `src/pipeline_doctor/jenkins.py` (new)

- Mirror the structure of `github.py`: module-level `_SafeRedirect` (import it from `github` —
  do not duplicate), `_OPENER`, a single `_request(url, user, token, accept, timeout=60)` seam
  returning the response object (so `console_log` can stream).
- Basic auth header built with `base64.b64encode(f"{user}:{token}".encode()).decode()`.
- `console_log`: read `resp.read(65536)` in a loop; keep `head` (first `head_bytes`) and a
  `bytearray` tail trimmed to `max_tail_bytes`; count dropped bytes; join with
  `\n[... N bytes omitted ...]\n`; decode `utf-8`, `errors="replace"`; then `strip_console_notes`.
- Map `HTTPError` 401/403 to `JenkinsError` mentioning `PD_JENKINS_USER`; re-raise others as
  `JenkinsError(f"HTTP {code} on {path}")` where `path` excludes the base URL's userinfo (there
  must be none: reject a base URL containing `@` in `check_base_url`).

### V3 — `src/pipeline_doctor/agent.py` (extend)

- Extract the engine selection currently inline in `poll_once` into
  `_diagnose(raw, cleaned, session_id, metadata) -> tuple[dict | None, str, str]`
  returning `(diagnosis, status, source)`; `source` is `"llm"`, `"rules"` or `""` (LLM error).
  It must call `ask_ollama` / `run_graph` through the module-level names so existing patches work.
  `poll_once` then uses it — its observable behaviour must not change.
- Add `poll_jenkins_once(base, jobs, user, token, db_path, webhook, dry_run=False, per_job=10,
  max_log_bytes=2_097_152) -> int` following the spec "Jenkins flow". Import Jenkins functions
  into `agent`'s namespace (`from .jenkins import JenkinsError, build_url, console_log,
  list_failed_builds`) so tests can patch `agent.list_failed_builds` etc.
- `main`: parse `PD_SOURCES` (default `"github"`, split on `,`, strip, reject unknown). Validate
  per source; for `github` keep the exact v1 check and message. Each pass:
  `for source in sources: try: <poll> except Exception as exc: print(stderr)` — isolation.
  `--interval` loop unchanged in shape.

### V4 — `rules.py`, `graph.py`, `evals.py`, `llm.py` (small moves)

- Move `grounding()` from `graph.py` to `rules.py` unchanged; in `graph.py` replace the
  definition with `from .rules import grounding`.
- Move `UNSAFE_FIX` from `evals.py` to `rules.py` unchanged; `from .rules import UNSAFE_FIX` in
  `evals.py`.
- `llm.chat(system_prompt, log, timeout=180, model=None, user_template="Log du build en echec :\n\n{log}")`
  → user content `user_template.format(log=log)`. Careful: `.format` on a template is fine, but
  never call `.format` on the *log* itself (braces in logs).

### V5–V7 — `src/pipeline_doctor/fixer.py` (new, pure)

- No network, no file I/O, no environment reads. The LLM is the `ask(system, user) -> dict`
  argument. Constants and signatures exactly as in the spec.
- `is_denied`: lower-case the path; reject `..` segments and leading `/`; `fnmatch` each pattern
  against the full path **and** the basename; `.github/*` and `.circleci/*` match at any depth
  (check every path segment).
- `candidate_paths`: build a suffix index of tree paths once (`{tail: [paths]}` for every
  `/`-boundary tail) for the longest-suffix match; strip `:<line>[:<col>]` and `:[l,c]`.
- `apply_edits`: LF-normalise (`text.replace("\r\n", "\n")`), apply, then restore CRLF and the
  final-newline state per file.
- `render_diff`: `difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
  f"a/{p}", f"b/{p}", n=3)` on LF-normalised text, files sorted.
- `guard`: implement the table in the spec in order; added lines = diff lines starting with `+`
  but not `+++`.
- `propose` = `build_prompt` → `ask(FIX_SYSTEM_PROMPT, user)` (wrap any exception as
  `FixRejected("no_fix", "llm_error")`) → `parse_edits` → `apply_edits` → `render_diff` → `guard`
  → `Proposal`.
- `FIX_SYSTEM_PROMPT` may be in French like `llm.SYSTEM_PROMPT`; it must contain the keywords the
  V7 test asserts (`JSON`, `edits`, `search`, `replace`, `--force`, and a "do not disable tests"
  rule).

### V8 — `src/pipeline_doctor/github.py` (extend, GET only)

- Add `get_tree`, `get_file`, `branch_sha`, `open_fix_prs` per spec, through the existing
  `_request`. Do not change existing functions.

### V9 — `src/pipeline_doctor/github_write.py` (new)

- Own `_request(method, url, token, body: dict | None)` seam (JSON body, same headers as
  `github.py`, same `_SafeRedirect` opener imported from `github`). Map `HTTPError` 422 on
  `git/refs` to `GitHubWriteError(422, "branch_exists")`; other errors to
  `GitHubWriteError(code, f"github_error:{code}")`.
- `sync_fork`, `commit_files`, `create_branch`, `open_draft_pr` exactly as specified. Nothing else.

### V10 — `fixflow.py` (new), `slack.py` (extend), `agent.py` (wire `propose`)

- `fixflow.run_fix` implements the spec flow top-down; each early exit builds the row
  `{source: "github", source_ref: str(run_id), repo, base_branch, base_sha, status, reason, files,
  model, prompt_version}` and returns it after `record_fix` (skipped in `dry_run`).
- Run-level checks use `run["head_repository"]["full_name"]`, `run["head_branch"]`,
  `run["head_sha"]`, `run["id"]` (fields of the workflow-run object returned by `list_runs`).
- `slack.build_fix_payload` per spec (French labels like the other payloads; diff in a code
  fence, truncated to `MAX_BLOCK_CHARS` from the start).
- `agent.main`: parse `PD_FIX_MODE` (`off` default; invalid → 2), build `FixSettings`, pass
  `fix=` to `poll_once` only when mode ≠ `off` and `github` is a source. In `poll_once`, after
  `record(...)`, `if fix: try: run_fix(...) except Exception: print(stderr)`.

### V11 — `pr` mode in `fixflow.py`

- Validate `PD_FIX_FORK` / `PD_FIX_TOKEN` and the `PD_FIX_TOKEN != GITHUB_TOKEN` rule in
  `agent.main`.
- After `guard`: `open_fix_prs` (upstream, read token) → `sync_fork` → `branch_sha(fork, …,
  write_token)` → `commit_files` → `create_branch` → `open_draft_pr`. Map `GitHubWriteError.reason`
  to the row (`status="error"`).
- Build title, commit message and body with the spec templates; implement
  `_fence(text) -> str` (fence = one more backtick than the longest run in `text`, min 3; text cut
  to 1 000 chars).

### V12 — docs

- `.env.example`, `README.md`, `deploy/README.md`, `CHANGELOG.md`, `docs/README.md` per ticket.
  Docs in French like the existing README; keep commands copy-pastable.

## Test fixtures

Follow `tests/test_agent.py` (patch names on the module under test, temp-dir DB) and
`tests/test_github.py` (redirect handler). Reusable fakes:

```python
FAKE_GITHUB_TOKEN = "ghp_" + "A" * 24          # never a literal secret

RUN = {"id": 101, "name": "CI", "head_branch": "main", "head_sha": "abc123",
       "conclusion": "failure", "event": "push",
       "head_repository": {"full_name": "A-Djihadi/pipeline-doctor"}}

DIAG = {"category": "compilation", "cause": "Missing method foo()",
        "evidence": ["[ERROR] App.java:[12,5] cannot find symbol"], "fix": "Rename bar to foo",
        "confidence": 0.9}
LOG = "[INFO] build\n[ERROR] App.java:[12,5] cannot find symbol\n[ERROR] COMPILATION ERROR"
TREE = {"src/main/java/App.java": "100644", ".github/workflows/ci.yml": "100644"}
FILES = {"src/main/java/App.java": "class App {\n  void run() { bar(); }\n}\n"}

def fake_ask_ok(system, user):
    return {"edits": [{"path": "src/main/java/App.java", "search": "bar();", "replace": "foo();"}],
            "summary": "call foo", "confidence": 0.8}
```

For Jenkins, fake responses are `io.BytesIO` objects exposing `read(n)`; a 10 MiB body can be a
generator-backed object to prove chunked reading.

## Files touched

- `src/pipeline_doctor/journal.py` — V1 (extend).
- `src/pipeline_doctor/jenkins.py` — **new**, V2.
- `src/pipeline_doctor/agent.py` — V3, V10, V11 (extend; v1 path behaviour unchanged).
- `src/pipeline_doctor/rules.py`, `graph.py`, `evals.py`, `llm.py` — V4 (moves + one keyword arg).
- `src/pipeline_doctor/fixer.py` — **new**, V5–V7.
- `src/pipeline_doctor/github.py` — V8 (add GET helpers only).
- `src/pipeline_doctor/github_write.py` — **new**, V9.
- `src/pipeline_doctor/fixflow.py` — **new**, V10–V11.
- `src/pipeline_doctor/slack.py` — V10 (add `build_fix_payload` only).
- `tests/test_journal_v2.py`, `test_jenkins.py`, `test_agent_v2.py`, `test_llm.py`,
  `test_fixer.py`, `test_github_read.py`, `test_github_write.py`, `test_fixflow.py` — **new**;
  `tests/test_rules.py` — V4 additions.
- `.env.example`, `README.md`, `deploy/README.md`, `CHANGELOG.md`, `docs/README.md` — V12.

Do **not** touch `cli.py`, `notify.py`, `sanitizer.py`, `tracing.py`, `.github/workflows/*`,
the `diagnostics` schema, or the v1 test files. Do not add dependencies. Do not add a
merge/approve/ready capability, even behind a flag.

## Definition of done (per PR)

- All RED criteria of the shipped tickets green in both CI jobs; v1 test files unchanged
  (`git diff --exit-code origin/main -- tests/test_agent.py tests/test_notify.py
  tests/test_sanitizer.py tests/test_journal.py tests/test_github.py` quoted in the PR body).
- `python -m pipeline_doctor.agent --once --dry-run` with no v2 variable prints the same output
  as on `main` for the same mocked inputs.
- Gitleaks job green; no secret-looking literal added.
- The "to verify" items of the spec that the ticket touches are either confirmed (with a doc
  link in the PR) or still listed as open.
