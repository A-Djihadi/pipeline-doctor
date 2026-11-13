# Spec — pipeline-doctor journal (diagnostic history UI)

## Problem Statement

A user of pipeline-doctor can only consult the diagnostic history by opening the raw SQLite file
(`diagnostics.db`) with the `sqlite3` shell. There is no supported, read-only, zero-dependency
way to list past diagnoses, filter them by repo or status, inspect a single run, or open a
visual view in a browser.

## Solution

Add a read-only `journal` subcommand to the existing CLI and a static HTML export, backed by a
new read-only view layer. `python -m pipeline_doctor journal` lists recent diagnostics,
`journal show <run_id>` prints one run's full detail, and `journal --html` writes a
self-contained page (inline CSS, zero JS) that opens in any browser. The existing
`python -m pipeline_doctor <logfile>` diagnostic command is unchanged.

## User Stories

1. As a maintainer, I want to list the most recent CI failures that were diagnosed, so that I
   can see what has been going wrong without opening a database.
2. As a maintainer, I want to filter the list by repository, so that I can focus on one project.
3. As a maintainer, I want to filter the list by status (e.g. `llm_error`), so that I can find
   runs whose diagnosis failed.
4. As a maintainer, I want to cap the number of rows with `--limit`, so that a long history does
   not flood my terminal.
5. As a maintainer, I want to see the full detail of one run (`show`), so that I can read the
   cause, the fix, and the evidence lines behind a summary row.
6. As a maintainer, I want an empty journal to be reported gracefully, so that a first run does
   not look like a crash.
7. As a maintainer, I want to export the filtered history to a single HTML file, so that I can
   open it in a browser or attach it to a ticket without running any server.
8. As a maintainer, I want the HTML export to be safe, so that a malicious log line or LLM output
   cannot inject markup or scripts into the page I open.
9. As a maintainer, I want the existing `pipeline_doctor <logfile>` command to keep working
   unchanged, so that this feature is purely additive.
10. As a maintainer, I want to point the viewer at a specific database file (`--db`), so that I
    can inspect a backup or a shared journal.

## CLI contract

The command is `python -m pipeline_doctor journal <subcommand> [options]`.

```
journal [--db PATH] [--repo REPO] [--status STATUS] [--limit N] [--html] [--out PATH]
journal show <run_id> [--db PATH]
```

### Flags

| Flag | Applies to | Meaning |
|---|---|---|
| `--db PATH` | both | SQLite file to read. Default: `$PD_DB`, else `diagnostics.db` (same default as the agent). |
| `--repo REPO` | list / html | Exact-match filter on `repo` (e.g. `A-Djihadi/pipeline-doctor`). |
| `--status STATUS` | list / html | Exact-match filter on `status` (e.g. `diagnosed`, `llm_error`). |
| `--limit N` | list / html | Max rows, positive integer, default `20`. |
| `--html` | list | Emit the self-contained HTML page instead of the text table (stdout, or `--out`). |
| `--out PATH` | list | Write the HTML page to PATH instead of stdout. Only valid with `--html`. |

`journal` with no subcommand is the list (text table). `show` takes exactly one positional
`<run_id>` and ignores `--repo`, `--status`, `--limit`, `--html`, `--out`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success — including an empty journal or a successful HTML export. |
| 1 | `show` requested a `run_id` that does not exist. |
| 2 | Usage error (argparse) or the database file exists but cannot be opened/read. |

## Output formats

### Text table (`journal`, no `--html`)

Columns, left-aligned: `RUN_ID`, `STATUS`, `CONF`, `REPO`, `WORKFLOW`, `BRANCH`, `CAUSE`
(cause truncated to a single line, ~60 chars, terminated with `…` when cut). One row per
diagnostic, ordered newest first (`processed_at DESC, run_id DESC`). `CONF` is the confidence
rounded to two decimals, or `-` when `confidence` is `NULL`.

Empty list prints exactly: `No diagnostics recorded.`

### Text detail (`journal show <run_id>`)

One labelled line per field, then the evidence as a bullet list:

```
run_id      : 101
repo        : A-Djihadi/pipeline-doctor
workflow    : ci.yml
branch      : main
status      : diagnosed
cause       : <cause>
confidence  : 0.90
fix         : <fix>
processed_at: 2026-11-10T12:00:00Z
evidence:
  - <line>
  - <line>
```

Fields that are `NULL` or empty (`cause`, `confidence`, `fix`, `evidence`) render as `-` (or,
for `evidence`, no bullets). A `llm_error` / `no_failed_job` row therefore shows `status:
llm_error`, `cause: -`, `confidence: -`, `fix: -`, and no evidence, without erroring.

### HTML (`journal --html`)

A single self-contained file:

- `<!doctype html>` + `<meta charset="utf-8">`, an inline `<style>` block, **no** `<script>`,
  **no** external `<link>`/`<img>`/`<iframe>`.
- A header with the page title and the generated-at timestamp (and the DB path when available).
- One `<table>` with the same columns as the text table; the full (untruncated) `cause` and
  `fix` in the cells.
- Evidence rendered per row as a native `<details><summary>Evidence (N)</summary><ul>…</ul>`
  disclosure — zero JS, so it works offline and under a strict content-security policy.
- Every field value passed through `html.escape` (untrusted input: log lines, LLM output, repo
  names). `confidence` `NULL` renders `-`.
- Empty list renders the same skeleton with the text `No diagnostics recorded.`

## Edge cases

- **Journal missing**: `--db` points at a file that does not exist → treated as empty, print
  `No diagnostics recorded.`, exit 0. The viewer never creates a database.
- **Journal empty**: file exists with schema but no rows → same as above.
- **`llm_error` / `no_failed_job` rows**: `cause`, `confidence`, `fix`, `evidence` are
  `NULL`/empty → rendered as `-` / no bullets; never a crash.
- **Malformed evidence**: `evidence` is not valid JSON or missing → decoded as `[]`.
- **`confidence` NULL**: rendered as `-` in both text and HTML.
- **HTML injection**: any field containing `<script>`, `<`, `&` is escaped in the HTML export.
- **Non-ASCII**: accented cause/fix/evidence lines are preserved (UTF-8) in both formats.
- **`--limit` <= 0**: rejected by `argparse` (type-checked positive integer).
- **`--out` without `--html`**: rejected by `argparse`.
- **`show` with unknown `run_id`**: message on stderr, exit 1.

## Implementation Decisions

- New module `journal_view.py`: pure read-only functions that take an already-open
  `sqlite3.Connection` — `list_diagnostics(conn, *, repo=None, status=None, limit=20)`,
  `get_diagnostic(conn, run_id)`, `format_table(rows)`, `format_detail(row)`,
  `render_html(rows, *, db_path="")`, plus `open_readonly(path)` returning a read-only
  connection or `None` when the file is missing.
- Row shape (dict) returned by the query functions:
  `{run_id, repo, workflow, branch, cause, confidence, fix, evidence: list, status,
  processed_at}` with `evidence` already decoded.
- `cli.py` dispatches on the first argument: if it is `journal`, route to the journal parser;
  otherwise run the existing legacy diagnostic path unchanged (backward compatibility).
- `status` is opaque: the viewer never defines or validates a closed enum.
- DB default resolution: `os.environ.get("PD_DB", "diagnostics.db")` — identical to the agent,
  so the viewer reads what the agent writes.

## Testing Decisions

- Test external behavior only (rows returned, rendered strings, exit codes), not private
  helpers.
- In-memory fixtures: `conn = journal.init(":memory:")` then `journal.record(...)`; pass the
  same `conn` to the view functions. (Temp-file fixtures via `tempfile.TemporaryDirectory()`
  are equally acceptable and match `tests/test_journal.py`.)
- Run with `PYTHONPATH=src python -m unittest discover -s tests -v`; stdlib only, no new
  dependencies.
- For the HTML tests assert on presence/absence of markers (`<!doctype html>`, `</html>`,
  `&lt;script&gt;`, no `<script>`, no `http://`/`https://` external asset), not on exact byte
  output, so tests survive minor CSS changes.
- CLI tests use `unittest.mock.patch` on the network/model boundaries the same way
  `tests/test_agent.py` does, and capture `sys.stdout`/`sys.stderr` with `redirect_stdout`.

## Out of Scope

- The demo video and the article (later roadmap items).
- Any mutation of the journal: re-diagnosis, re-notification, deduplication changes, migration.
- The `http.server` mini-dashboard (rejected in ADR-0006).
- A `delete`/`prune`/`clear` command: the viewer is read-only.
- HTML pagination, sorting UI, search box, or any JavaScript interactivity.

## Further Notes

- `show` and `--html` share the same query layer, so filters and the empty-state behave
  identically across formats.
- The HTML export is deliberately dependency-free and offline-capable so it can be the visual
  artifact used by the later demo video without standing up any service.
