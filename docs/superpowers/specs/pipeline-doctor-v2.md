# Spec — pipeline-doctor v2 (Jenkins source + fix proposals as draft PRs)

Decisions: [ADR-0007](../../adr/0007-jenkins-ingestion-poll.md) (Jenkins ingestion),
[ADR-0008](../../adr/0008-fix-pr-human-validation.md) (fix PR + human validation),
[ADR-0009](../../adr/0009-v2-backward-compatibility.md) (backward compatibility).
Tickets: [`docs/tickets-v2.md`](../../tickets-v2.md). Coder brief: [`docs/coder-brief-v2.md`](../../coder-brief-v2.md).

## Problem Statement

1. pipeline-doctor only sees GitHub Actions. Teams whose builds run on **Jenkins** get no
   diagnosis at all.
2. When the diagnosis is right, a human still has to turn the prose "suggested fix" into a code
   change. The agent could **propose the change itself** — but an LLM-written change derived
   from an untrusted CI log must never reach a branch, a secret, or a merge without a human.

## Solution

- **Jenkins source**: the self-hosted agent polls configured Jenkins jobs over the JSON API,
  downloads the plain-text console of each new `FAILURE` build, and runs it through the
  unchanged diagnosis core (sanitize → Ollama/graph → Slack → journal).
- **Fix proposals**: for an eligible failed GitHub Actions run, the agent asks Ollama for a
  minimal search/replace patch on a few files read through the API, checks it with deterministic
  guards, and either posts the diff to Slack (`propose`) or opens a **draft PR from a bot-owned
  fork** (`pr`). Only a human can run the PR's CI, mark it ready, approve and merge.
- Both are **off by default**; v1 behaviour is unchanged until configured.

## User Stories

1. As a maintainer using Jenkins, I want failed Jenkins builds diagnosed and posted to Slack like
   GitHub runs, so that one tool covers both CI systems.
2. As a maintainer, I want the agent to read Jenkins with a read-only account and never trigger,
   cancel or configure anything, so that enabling it is low-risk.
3. As a maintainer, I want one unreachable CI system not to stop diagnoses for the other.
4. As a maintainer, I want each Jenkins build diagnosed exactly once, even across restarts.
5. As a maintainer, I want to see a concrete patch for a diagnosed failure without granting any
   write access (`propose` mode).
6. As a maintainer, I want the agent to open a draft PR with that patch, so that I can review it
   with normal GitHub tools (`pr` mode).
7. As a maintainer, I want it to be impossible for the agent to merge, approve or run CI on its
   own PR, so that no unreviewed code lands.
8. As a maintainer, I want LLM-written code never to run in CI with my repository secrets before
   I approve it.
9. As a maintainer, I want the agent never to touch CI definitions, secret files or lockfiles,
   and never to commit anything that looks like a secret.
10. As a maintainer, I want the agent to refuse to propose bypasses (`--force`, skipped tests,
    TLS off), so that proposals fix causes, not symptoms.
11. As a maintainer, I want at most one open fix PR per branch and a daily cap, so that a flaky
    period does not flood me with PRs.
12. As a maintainer, I want every proposal, rejection and its reason journaled, so that I can
    measure how useful fix generation is.
13. As an existing user, I want my CLI, `diagnose.yml` workflow and agent to behave exactly as
    before if I set no new variable.

## Configuration contract

All configuration is environment variables (same as v1). New variables:

| Variable | Default | Meaning |
|---|---|---|
| `PD_SOURCES` | `github` | Comma-separated subset of `github`, `jenkins`. Unknown name → exit 2. |
| `PD_JENKINS_URL` | — | Jenkins base URL, e.g. `https://jenkins.example.com`. `https` required unless host is `localhost`, `127.0.0.1` or `::1`. Required if `jenkins` enabled. |
| `PD_JENKINS_JOBS` | — | Comma-separated job paths, `/` between folder levels: `team/app/main`. Required if `jenkins` enabled. |
| `PD_JENKINS_USER` | — | Jenkins user of the read-only account. Required if `jenkins` enabled. |
| `PD_JENKINS_TOKEN` | — | That user's API token. Required if `jenkins` enabled. Never logged. |
| `PD_JENKINS_PER_JOB` | `10` | Newest builds examined per job per pass. |
| `PD_JENKINS_MAX_LOG_BYTES` | `2097152` | Tail bytes of `consoleText` kept (plus the first 64 KiB). |
| `PD_FIX_MODE` | `off` | `off`, `propose`, `pr`. Unknown value → exit 2. Ignored (stderr warning) if `github` is not in `PD_SOURCES`. |
| `PD_FIX_FORK` | — | `owner/repo` of the bot's fork. Required in `pr` mode. |
| `PD_FIX_TOKEN` | — | Bot's classic PAT, scope `public_repo` only. Required in `pr` mode; must differ from `GITHUB_TOKEN` (else exit 2). |
| `PD_FIX_MAX_PER_DAY` | `3` | Max proposals with status `proposed`/`pr_opened` per repo in the last 24 h. |

`GITHUB_TOKEN` (read token) needs `Contents: read` in addition to `Actions: read` for
`propose`/`pr` modes (it reads the tree and files of the failing commit). It is never used for a
write.

Exit codes of `python -m pipeline_doctor.agent`: `0` normal, `2` configuration error (message on
stderr naming the missing/invalid *variable*, never its value). With all v2 variables unset, the
only check and message are v1's (`Variables manquantes : PD_REPO et GITHUB_TOKEN.`).

## Architecture

```
                 ┌──────────── agent.main (PD_SOURCES, PD_FIX_MODE) ────────────┐
                 │                                                               │
   github source │ poll_once (v1, unchanged path)        poll_jenkins_once (new) │ jenkins source
   github.py GET ─┤   list_runs/list_jobs/job_log            jenkins.py GET        ├─ list_failed_builds
                 │        │                                       │               │  console_log
                 │        ▼                                       ▼               │
                 │   _diagnose(raw) ── sanitize → ask_ollama | graph.run ──┐      │
                 │        │                                                │      │
                 │   Slack build_diagnosis_payload  ◄──────────────────────┘      │
                 │   journal.record (diagnostics)      journal.record_build (jenkins_diagnostics)
                 │        │
                 │        ▼  (PD_FIX_MODE != off, github only, after the diagnosis is recorded)
                 │   fixflow.run_fix(run, diagnosis, cleaned, settings)
                 │        ├─ fixer.eligibility / candidate_paths / propose / guard   (pure, stdlib)
                 │        ├─ github.py  GET tree, files, branch tip, open PRs        (read token)
                 │        ├─ github_write.py  fork sync, commit, ref, draft PR       (bot token, pr mode)
                 │        ├─ journal.record_fix (fix_proposals)
                 │        └─ Slack build_fix_payload (second message)
                 └───────────────────────────────────────────────────────────────┘
```

### Module responsibilities and interfaces

| Module | New/changed | Responsibility | Network |
|---|---|---|---|
| `jenkins.py` | new | Jenkins read client (ADR-0007). | `GET` only |
| `github.py` | extended | v1 functions unchanged + read-only helpers for the fix flow. | `GET` only |
| `github_write.py` | new | The **only** module that issues non-`GET` requests to GitHub. | fork writes + `POST /pulls` |
| `fixer.py` | new | Pure fix logic: eligibility, candidates, path policy, prompt, parsing, apply, guards, diff. No I/O; the LLM is an injected callable. | none |
| `fixflow.py` | new | Orchestration of one fix attempt; maps every outcome to a journal row and a Slack message. | via the modules above |
| `journal.py` | extended | Two new tables + accessors; `diagnostics` untouched. | — |
| `slack.py` | extended | New `build_fix_payload`; existing builders untouched. | — |
| `rules.py` | extended | Receives `grounding()` (from `graph.py`) and `UNSAFE_FIX` (from `evals.py`), both re-exported at their old location. | — |
| `llm.py` | extended | `chat(..., user_template="Log du build en echec :\n\n{log}")`. | Ollama |
| `agent.py` | extended | `PD_SOURCES`, `poll_jenkins_once`, optional `fix=` on `poll_once`, shared `_diagnose`. | — |

#### `jenkins.py`

```python
class JenkinsError(Exception): ...

def check_base_url(base: str) -> str
    # strips trailing "/", raises JenkinsError if scheme is not https (loopback http allowed)
    # or if the URL carries userinfo ("user:pass@") — credentials only via PD_JENKINS_USER/TOKEN
def job_url(base: str, job_path: str) -> str
    # "team/app/main" -> f"{base}/job/team/job/app/job/main"; each segment quoted with
    # urllib.parse.quote(seg, safe="%") so a pre-encoded "%2F" survives (to verify on instance)
def build_url(base: str, job_path: str, number: int) -> str      # job_url(...) + f"/{number}/"
def list_failed_builds(base, job_path, user, token, per_job=10) -> list[int]
    # GET {job_url}/api/json?tree=builds[number,result,building]
    # sort by number desc, keep per_job, return numbers with building is False and result == "FAILURE"
def console_log(base, job_path, number, user, token,
                max_tail_bytes=2_097_152, head_bytes=65_536) -> str
    # GET {build_url}consoleText, streamed in 64 KiB chunks; keeps head + rolling tail;
    # inserts "\n[... N bytes omitted ...]\n" when it dropped bytes; decodes utf-8 errors="replace";
    # returns strip_console_notes(text)
def strip_console_notes(text: str) -> str
    # removes r"\x1b\[8mha:[^\x1b]*\x1b\[0m" (Jenkins ConsoleNote; format to verify)
```

All requests: `Authorization: Basic base64(user:token)`, `Accept: application/json` (or
`text/plain` for the console), `User-Agent: pipeline-doctor`, timeout 60 s, opener with the
`_SafeRedirect` rule (drop `Authorization` on cross-host redirect). HTTP 401/403 raise
`JenkinsError("Jenkins refused credentials for PD_JENKINS_USER (HTTP 403)")` — never the token.

#### `github.py` additions (GET only, read token)

```python
def get_tree(repo, sha, token) -> tuple[dict[str, str], bool]
    # GET /repos/{repo}/git/trees/{sha}?recursive=1 -> ({path: mode} for type == "blob", truncated)
def get_file(repo, path, sha, token) -> str | None
    # GET /repos/{repo}/contents/{quote(path, safe="/")}?ref={sha}; base64-decode; None if not UTF-8
def branch_sha(repo, branch, token) -> str
    # GET /repos/{repo}/branches/{quote(branch, safe="/")} -> commit.sha
def open_fix_prs(repo, base, fork_owner, token) -> list[int]
    # GET /repos/{repo}/pulls?state=open&base={base}&per_page=100
    # -> numbers where head.repo.owner.login == fork_owner and head.ref startswith "pipeline-doctor/fix-"
```

#### `github_write.py` (bot token, `pr` mode only)

```python
class GitHubWriteError(Exception): status: int; reason: str

def sync_fork(fork, branch, token) -> None             # POST /repos/{fork}/merge-upstream {"branch": branch}
def commit_files(fork, parent_sha, files: dict[str, str], message, token) -> str
    # GET  /repos/{fork}/git/commits/{parent_sha}            -> tree.sha
    # POST /repos/{fork}/git/blobs   {"content", "encoding": "utf-8"}   (one per file)
    # POST /repos/{fork}/git/trees   {"base_tree", "tree": [{"path","mode":"100644","type":"blob","sha"}]}
    # POST /repos/{fork}/git/commits {"message", "tree", "parents": [parent_sha]}  -> commit sha
def create_branch(fork, branch, sha, token) -> None    # POST /repos/{fork}/git/refs {"ref": "refs/heads/"+branch, "sha"}
def open_draft_pr(upstream, head: str, base, title, body, token) -> tuple[int, str]
    # POST /repos/{upstream}/pulls {"title","head","base","body","draft": true,"maintainer_can_modify": true}
    # -> (number, html_url). Raises GitHubWriteError("draft_refused") if response "draft" is not true.
```

There is **no parameter** to open a non-draft PR and **no function** for merge (`/pulls/{n}/merge`),
reviews, ready-for-review, labels, comments, branch deletion or any upstream write except
`POST /pulls`. A test enforces the allow-list of `(method, path pattern)` pairs (see Testing).

#### `fixer.py` (pure)

```python
FIXABLE_CATEGORIES = frozenset({"compilation", "dependency", "tests"})
MAX_FILES = 3; MAX_FILE_CHARS = 20_000; MAX_CHANGED_LINES = 40; MIN_CONFIDENCE = 0.6
FIX_PROMPT_VERSION = "fix-v1"
BRANCH_PREFIX = "pipeline-doctor/fix-"

class FixRejected(Exception):
    def __init__(self, status: str, reason: str): ...   # status in {"skipped","no_fix","rejected"}

@dataclass(frozen=True)
class Edit: path: str; search: str; replace: str

@dataclass(frozen=True)
class Proposal:
    files: tuple[str, ...]        # edited paths, sorted
    original: dict[str, str]      # path -> original text (as fetched)
    patched: dict[str, str]       # path -> patched text (original EOL style restored)
    diff: str                     # unified diff, "a/<path>" "b/<path>", 3 lines of context
    summary: str                  # model summary, <= 200 chars, single line
    confidence: float

def eligibility(diagnosis: dict | None, log: str, *, source: str) -> str | None
def is_denied(path: str) -> bool
def candidate_paths(log: str, tree: dict[str, str], category: str) -> list[str]
def select_files(fetched: dict[str, str | None]) -> dict[str, str]          # raises FixRejected
def build_prompt(diagnosis: dict, log: str, files: dict[str, str]) -> str   # user message
def parse_edits(obj: dict) -> tuple[list[Edit], str, float]                 # raises FixRejected
def apply_edits(files: dict[str, str], edits: list[Edit]) -> dict[str, str] # raises FixRejected
def render_diff(original: dict[str, str], patched: dict[str, str]) -> str
def guard(original: dict[str, str], patched: dict[str, str], diff: str) -> None  # raises FixRejected
def propose(diagnosis: dict, log: str, files: dict[str, str], ask) -> Proposal
    # ask(system: str, user: str) -> dict ; production: lambda s, u: llm.chat(s, u, user_template="{log}", timeout=300)[0]
```

#### `fixflow.py`

```python
@dataclass(frozen=True)
class FixSettings:
    mode: str                 # "propose" | "pr"
    read_token: str           # GITHUB_TOKEN
    fork: str | None          # PD_FIX_FORK (pr)
    write_token: str | None   # PD_FIX_TOKEN (pr)
    max_per_day: int = 3

def run_fix(conn, repo: str, run: dict, diagnosis: dict | None, source: str,
            cleaned_log: str, settings: FixSettings, webhook: str, dry_run: bool,
            *, ask=None) -> dict
    # returns the fix_proposals row it recorded (or would record, in dry_run). Never raises:
    # any unexpected exception -> status "error", reason "exception:<ClassName>".
```

`agent.poll_once(..., fix: FixSettings | None = None)` calls `run_fix` **after** `record(...)` of
the diagnosis, inside `try/except Exception` (defence in depth), only for runs where a diagnosis
attempt was made (not for `no_failed_job`).

## Jenkins flow (per pass, per job in `PD_JENKINS_JOBS`)

1. `list_failed_builds(...)` → build numbers (newest first).
2. For each number: `key = build_url(base, job, number)`; skip if `journal.is_build_processed`.
3. `raw = console_log(...)`; `cleaned, n = sanitize(raw, 12000)`.
4. `diagnosis, status = _diagnose(raw, cleaned, session_id=key, metadata={"source": "jenkins", "job": job, "build": number})`
   — identical engine selection as GitHub (`PD_ENGINE`).
5. Slack: `build_diagnosis_payload(f"jenkins:{job}", "Jenkins", "-", number, key, n, diagnosis, cleaned)`.
6. `journal.record_build(conn, key, base, job, number, diagnosis, status)`; statuses as v1:
   `diagnosed`, `llm_error`, `rules_fallback`, `notify_error`.
7. Errors: `JenkinsError`/`URLError` on listing → stderr, continue with next job; on one build's
   console → stderr, build **not** recorded (retried next pass).
8. `--dry-run`: print the payload, record nothing (as v1).

## Fix flow (per eligible GitHub run)

```
diagnosis recorded ──► fixer.eligibility ──x skipped:<reason>
   │ run checks: head_repository.full_name == repo (else skipped:fork_run)
   │             head_branch not startswith BRANCH_PREFIX (else skipped:own_branch)
   │             github.branch_sha(repo, head_branch) == run.head_sha (else skipped:stale)
   │             [pr] open_fix_prs(repo, head_branch, fork owner) empty (else skipped:open_pr_exists)
   │             journal.fixes_since(repo, now-24h) < max_per_day (else skipped:daily_limit)
   ▼
get_tree(repo, head_sha) ── truncated ──x skipped:tree_truncated
   ▼
candidate_paths ── [] ──x skipped:no_candidate_files
   ▼
get_file × N ─► select_files ──x skipped:no_candidate_files | skipped:secret_in_source
   ▼
fixer.propose (Ollama) ──x no_fix:llm_error | no_fix:bad_json | no_fix:no_edits
   │                    ──x rejected:<unknown_path|search_not_found|search_not_unique|no_change>
   ▼
fixer.guard ──x rejected:<too_many_files|too_many_lines|denied_path|secret_in_patch|redacted_placeholder|unsafe_fix>
   ▼
mode propose ──► journal proposed + Slack fix message (diff excerpt)
mode pr ──► sync_fork(fork, head_branch) ; branch_sha(fork, head_branch, bot token) == head_sha (else error:fork_out_of_sync)
            commit_files(fork, head_sha, patched, message) ; create_branch(fork, BRANCH_PREFIX+run_id) (422 → error:branch_exists)
            open_draft_pr(repo, f"{fork_owner}:{branch}", head_branch, title, body) (draft refused → error:draft_refused)
            ──► journal pr_opened (+pr_number, pr_url) + Slack fix message with PR link
```

`--dry-run`: the flow runs up to and including `guard`, prints the Slack fix payload, writes
nothing to GitHub and records nothing.

### Eligibility (`fixer.eligibility`) — first failing rule wins, in this order

| # | Rule | Reason code |
|---|---|---|
| 1 | `diagnosis` is not None and `source == "llm"` (graph `source`; linear: `ask_ollama` succeeded) | `not_llm` |
| 2 | `diagnosis["category"] in FIXABLE_CATEGORIES` | `category:<value>` |
| 3 | `float(confidence) >= MIN_CONFIDENCE` | `low_confidence` |
| 4 | `rules.grounding(diagnosis, log) == 1.0` | `ungrounded` |
| 5 | `rules.classify(log)["category"] in ("unknown", diagnosis["category"])` | `category_disagreement` |

`config` is deliberately not fixable: its fixes live in CI settings and secrets, which the bot must
never touch. `network`, `oom`, `disk`, `registry_auth` are infrastructure, not code.

### Candidate files (`fixer.candidate_paths`)

1. Scan `rules.error_windows(log)` then the full `log` for path-like tokens:
   `[\w./\\-]+\.(py|pyi|java|kt|kts|scala|groovy|js|jsx|mjs|cjs|ts|tsx|go|rs|rb|php|cs|c|h|cc|cpp|hpp|swift|xml|json|toml|ya?ml|cfg|ini|txt|gradle|properties|sql)`
   (backslashes normalised to `/`, a trailing `:<line>[:<col>]` or `:[<line>,<col>]` ignored).
2. Map each token to a tree path by **longest suffix match on a `/` boundary** (handles absolute
   runner paths like `/home/runner/work/x/x/src/a.py` without knowing the prefix). Ambiguous
   suffix (two tree paths of equal length) → token ignored.
3. For category `dependency`, append root manifests that exist in the tree, in this order:
   `package.json`, `requirements.txt`, `pyproject.toml`, `setup.cfg`, `pom.xml`, `build.gradle`,
   `build.gradle.kts`, `go.mod`, `Cargo.toml`, `Gemfile`, `composer.json`.
4. Keep paths in first-seen order, dedupe, drop `is_denied(path)`, drop mode ≠ `100644`, cap at
   `MAX_FILES`.

### Path denylist (`fixer.is_denied`, case-insensitive, `fnmatch` on the full path and basename)

`.github/*` (any depth), `.gitlab-ci.yml`, `.circleci/*`, `Jenkinsfile*`, `azure-pipelines*.yml`,
`.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `*secret*`,
`*credential*`, `*.tfstate*`, `*kubeconfig*`, `package-lock.json`, `npm-shrinkwrap.json`,
`yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`, `Pipfile.lock`, `uv.lock`, `Cargo.lock`,
`go.sum`, `Gemfile.lock`, `composer.lock`, `CODEOWNERS`, `.gitmodules`, `.gitattributes`,
`.pre-commit-config.yaml`, any path containing a `..` segment or starting with `/`.

### File selection (`fixer.select_files`)

From `{path: text|None}`: drop `None` (non-UTF-8 or missing) and texts longer than
`MAX_FILE_CHARS`; drop any text where `sanitizer.redact(text)[1] > 0`. Nothing left →
`FixRejected("skipped", "secret_in_source")` if at least one drop was due to a secret, else
`FixRejected("skipped", "no_candidate_files")`.

### Model contract

System prompt `FIX_SYSTEM_PROMPT` (in `fixer.py`, wording free, must state all of the following):
answer with JSON only `{"edits": [{"path": "...", "search": "...", "replace": "..."}],
"summary": "...", "confidence": 0.0}`; edit only the listed files; `search` must be copied
verbatim from the file and be unique in it; smallest change that removes the root cause;
`"edits": []` if no safe code change exists; never bypass (`--force`, `--legacy-peer-deps`,
`--no-verify`, skipped/disabled tests, TLS verification off, `chmod 777`); never add credentials,
tokens or URLs with credentials; never edit CI configuration.

User message (`fixer.build_prompt`):

```
Diagnostic
category: <category>
cause: <cause>
suggested fix: <fix>

Error lines (sanitized):
<rules.error_windows(log), last 4000 chars>

Files you may edit:
=== <path>
<text, LF line endings, no line numbers>
=== <path>
...
```

Parsing (`fixer.parse_edits`): `edits` must be a list of objects with string `path`, non-empty
string `search`, string `replace`; else `FixRejected("no_fix", "bad_json")`. Empty list →
`FixRejected("no_fix", "no_edits")`. `summary` → `str`, newlines replaced by spaces, cut to 200
chars; `confidence` → float clamped to [0, 1], default 0.

### Applying edits (`fixer.apply_edits`)

- Work on LF-normalised text. Remember per file whether the original used CRLF (more `\r\n` than
  bare `\n`) and whether it ended with a newline; restore both on the patched text.
- For each edit, in order: path not in `files` → `rejected:unknown_path`; `search` count in the
  current text == 0 → `rejected:search_not_found`; > 1 → `rejected:search_not_unique`; replace
  the single occurrence. Several edits on one file apply sequentially.
- Patched equal to original for every file → `rejected:no_change`.

### Guards (`fixer.guard`) — all checked, first violation raised

| Check | Reason code |
|---|---|
| changed files ≤ `MAX_FILES` | `too_many_files` |
| changed lines (`+`/`-` lines of the diff, headers excluded) ≤ `MAX_CHANGED_LINES` | `too_many_lines` |
| no changed path `is_denied` | `denied_path` |
| `redact(patched_text)[1] == 0` for each file **and** `redact(diff)[1] == 0` | `secret_in_patch` |
| `"[REDACTED"` not in any patched text | `redacted_placeholder` |
| no added diff line matches `rules.UNSAFE_FIX` | `unsafe_fix` |

### PR content (deterministic templates)

- Branch (fork): `pipeline-doctor/fix-<run_id>`.
- Commit message: `fix: <category> failure in run <run_id> (pipeline-doctor)` + blank line +
  `Proposed by pipeline-doctor with local model <model>. Human review required before merge.`
- PR title: `fix(pipeline-doctor): <category> failure on <base_branch> (run <run_id>)`.
- PR body (Markdown):

```
> [!WARNING]
> Generated by a **local LLM** (<model>, prompt <FIX_PROMPT_VERSION>) from a CI log.
> Review every line. pipeline-doctor never merges, approves or runs CI on this PR.

**Failed run:** <run_url> · **Commit:** <head_sha[:12]> · **Category:** <category>

### Diagnosis
<fenced: cause>

### Evidence (verbatim from the sanitized log)
<fenced: evidence lines>

### Model summary
<fenced: summary>

### Guards passed
eligibility · path denylist · secret scan (0 matches) · bypass patterns · ≤3 files / ≤40 lines

### How to validate
1. Read the diff. 2. Approve the fork workflow run (Actions). 3. If CI is green and the change
is right: mark ready, approve, merge. Otherwise close the PR.
```

Model-derived text (cause, evidence, summary) is placed only inside fenced blocks whose fence
is one backtick longer than the longest backtick run in the content (min 3); each field is cut to
1 000 chars. No model text in title, branch, or commit message.

### Slack fix message (`slack.build_fix_payload`)

```python
def build_fix_payload(repo, run_id, run_url, status, reason, summary, diff, pr_url=None) -> dict
```

Posted only for `proposed` and `pr_opened` (other outcomes are journaled, not notified — no
noise). Header `🛠️ Correctif propose : <repo>`; fields Run, Statut; `summary`; the diff
(already guarded) truncated to `MAX_BLOCK_CHARS` from the start inside a code fence; a PR link
section when `pr_url`. A failure to post does not change the proposal status.

## Journal schema (additive, ADR-0009)

```sql
CREATE TABLE IF NOT EXISTS jenkins_diagnostics (
    build_key    TEXT PRIMARY KEY,      -- build_url(base, job, number)
    instance     TEXT NOT NULL,         -- base URL
    job          TEXT NOT NULL,         -- job path as configured ("team/app/main")
    number       INTEGER NOT NULL,
    cause        TEXT,
    confidence   REAL,
    fix          TEXT,
    evidence     TEXT,                  -- JSON list
    status       TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fix_proposals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT NOT NULL,       -- "github" (v2)
    source_ref     TEXT NOT NULL,       -- run_id as text
    repo           TEXT NOT NULL,
    base_branch    TEXT,
    base_sha       TEXT,
    head_branch    TEXT,                -- pipeline-doctor/fix-<run_id> (pr mode)
    status         TEXT NOT NULL,       -- skipped | no_fix | rejected | proposed | pr_opened | error
    reason         TEXT,                -- reason code, NULL for proposed/pr_opened
    files          TEXT,                -- JSON list of edited (or candidate) paths
    diff           TEXT,                -- guarded diff, only for proposed/pr_opened
    pr_number      INTEGER,
    pr_url         TEXT,
    model          TEXT,
    prompt_version TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (source, source_ref)
);
```

Accessors: `is_build_processed(conn, build_key) -> bool`,
`record_build(conn, build_key, instance, job, number, diagnosis, status)`,
`record_fix(conn, row: dict)` (`INSERT OR REPLACE`, `created_at` set by the function),
`fixes_since(conn, repo, since_iso) -> int` (counts `proposed` + `pr_opened`).
The diff stored is the guarded one (0 secret matches by construction).

## Owner actions (one-off, outside the code)

Jenkins:
1. Create a dedicated Jenkins user with only `Overall/Read` + `Job/Read` on the watched jobs
   (exact permissions to verify with the instance's authorization strategy); generate an API
   token; store it in `/etc/pipeline-doctor.env` (chmod 600).

`propose` mode:
2. Add `Contents: Read-only` to the existing fine-grained read token.

`pr` mode (additionally):
3. Create a machine user (e.g. `pd-bot`), with 2FA, owned by the maintainer.
4. Fork the monitored repository into the machine user's account. Do **not** add the machine
   user as a collaborator on the upstream repository.
5. Create a **classic** PAT for the machine user, scope `public_repo` only, 90-day expiry; store
   as `PD_FIX_TOKEN` in `/etc/pipeline-doctor.env`. Set `PD_FIX_FORK=pd-bot/<repo>`.
6. Upstream Settings › Actions › General › fork PR workflows: **Require approval for all
   external contributors**.
7. Upstream ruleset on the default branch: PR required, ≥ 1 approval, dismiss stale approvals,
   required status checks (`test`, `test-agent-extra`, `secrets-scan`), no force-push/deletion,
   empty bypass list except the owner (DevSecOps baseline).
8. Verify once with `PD_FIX_MODE=pr python -m pipeline_doctor.agent --once --dry-run`, then a real
   pass on a deliberately broken branch.

## Edge cases

| Case | Behaviour |
|---|---|
| No v2 variable set | Byte-identical v1 behaviour (ADR-0009). |
| `PD_SOURCES=jenkins` only | `PD_REPO`/`GITHUB_TOKEN` not required; `PD_FIX_MODE` ignored with a warning. |
| Jenkins base URL is `http://jenkins.lan` | Exit 2 (`PD_JENKINS_URL must use https`). `http://localhost:8080` accepted. |
| Jenkins returns `result: null` / `building: true` | Not diagnosed; examined again next pass. |
| Jenkins 401/403 | Stderr message naming `PD_JENKINS_USER`; GitHub source still polled. |
| Huge console (500 MB) | Memory bounded to 64 KiB + `PD_JENKINS_MAX_LOG_BYTES`; omission marker inserted. |
| Same build number in two jobs | Distinct `build_key` (job path is part of the URL). |
| Jenkins response `url` points to another host | Ignored; credentialed URLs are always constructed. |
| Failed run is a fork PR | Diagnosed as v1; fix `skipped:fork_run`. |
| The bot's own PR fails CI | Diagnosed as v1 (it appears in upstream runs with a fork head); fix `skipped:fork_run`. |
| Branch moved since the failure | `skipped:stale` (the failure may already be fixed). |
| Tree response `truncated: true` | `skipped:tree_truncated` (v2 targets small/medium repos). |
| Model echoes a `[REDACTED:...]` placeholder | `rejected:redacted_placeholder`. |
| Model adds `-DskipTests` to `pom.xml` | `rejected:unsafe_fix`. |
| Model edits `.github/workflows/ci.yml` | Not offered as candidate; if named anyway → `rejected:unknown_path`; the bot token has no `workflow` scope either. |
| GitHub refuses draft (plan without drafts) | `error:draft_refused`; no ready PR is ever opened. |
| Fork branch ≠ failing SHA after sync | `error:fork_out_of_sync`; nothing pushed. |
| Fork branch `pipeline-doctor/fix-<run_id>` already exists | `error:branch_exists`; no PR. |
| Agent crashes after PR, before journal write | Diagnosis row exists → run never reprocessed → no duplicate PR. |
| `--dry-run` with `PD_FIX_MODE=pr` | No GitHub write, no journal write; payload printed. |

## Implementation Decisions

- Pure core / thin shell: all decisions (eligibility, candidates, apply, guards) are in
  `fixer.py`, pure functions tested with plain dicts. Network modules are thin and mocked at
  their single `_request` seam.
- Write capability is isolated in one module with an enforced allow-list; `github.py` stays
  `GET`-only (enforced by a test as well).
- Search/replace edits rather than unified diffs from the model: small models produce invalid
  hunks; exact-match replacement is verifiable and fails closed.
- No new dependency; no workflow change; no change to `sanitizer.py`, `cli.py`, `notify.py`.
- GitHub API version header stays `2022-11-28` like `github.py` (to verify it is still accepted
  for the new endpoints at implementation time).

## Testing Decisions

- Test external behaviour at module seams, not internals: `fixer` through its public functions;
  `jenkins`/`github`/`github_write` by patching their `_request` (or `_OPENER.open`) and asserting
  method, URL, headers and body; `agent`/`fixflow` by patching module-level names, as in
  `tests/test_agent.py`.
- Fake secrets are assembled at runtime (`"ghp_" + "A" * 24`), never literal (gitleaks).
- Safety properties get dedicated tests: GET-only for `github.py` and `jenkins.py`; write
  allow-list for `github_write.py`; `draft: true` always sent; no merge/review/ready endpoint
  string in `github_write.py` (source scan); credentials never in stderr; v1 test files
  untouched (`git diff --exit-code origin/main -- tests/test_agent.py tests/test_notify.py
  tests/test_sanitizer.py tests/test_journal.py tests/test_github.py` in the PR description).
- Everything runs in the stdlib-only CI job; nothing in v2 imports `langgraph`/`langfuse` at
  module level.

## Out of Scope

- Fix PRs for Jenkins builds (needs job → repo/SHA mapping; Git plugin `BuildData` to verify).
- Private target repositories in `pr` mode (needs `repo` scope or a GitHub App).
- Inbound webhook server for Jenkins or GitHub.
- Jenkins `UNSTABLE`/`ABORTED` builds, Blue Ocean / `wfapi` per-stage logs.
- Running the target project's build/tests locally to validate a patch.
- Syncing PR outcomes (merged/closed) back into the journal; journal viewer support for the new
  tables; Langfuse tracing and evals of fix generation (follow-ups).
- Signed commits for bot commits (API commits by a PAT are unsigned).

## Further Notes — to verify before/during implementation

1. Jenkins: `consoleText` behaviour with ConsoleNotes; required permissions; `%2F` in branch-job
   names; whether `builds` in `api/json` is capped (we only need the newest few).
2. GitHub: `merge-upstream` when the fork lacks the base branch (expected error → `error:fork_out_of_sync`);
   draft PR creation from a fork with a classic `public_repo` token; whether the `contents` API
   rate limits matter at 3 files per proposal (expected no).
3. Fork-PR approval setting name/location in the current GitHub UI.
