# Tickets — session 5 (journal des diagnostics, UI)

Read the spec (`docs/superpowers/specs/pipeline-doctor-journal.md`) and
ADR-0006 (`docs/adr/0006-journal-ui.md`) first. Every ticket is **test-first**: the acceptance
criteria below are the tests to write first (RED), then make green. Complexity S/M/L.
All tests run with:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Standard library only — no new dependency.

---

## T1 — Read-only query layer `journal_view.py` (M)

**What it delivers:** the two pure read functions the whole feature stands on — listing the
journal with filters/order/limit and fetching one run — each returning the decoded row shape.

**Blocked by:** None (can start immediately).

**RED acceptance criteria (tests in `tests/test_journal_view.py`):**

- [ ] `list_diagnostics(conn)` returns rows ordered by `processed_at DESC, run_id DESC`
      (insert rows out of order, assert the returned order).
- [ ] `list_diagnostics(conn, repo="a/b")` returns only rows whose `repo == "a/b"`.
- [ ] `list_diagnostics(conn, status="llm_error")` returns only rows with that status.
- [ ] `list_diagnostics(conn, limit=2)` returns at most 2 rows.
- [ ] `get_diagnostic(conn, run_id)` returns a dict with `evidence` already decoded as a list.
- [ ] `get_diagnostic(conn, unknown_id)` returns `None`.
- [ ] A row whose `evidence` column holds malformed JSON yields `evidence == []`, no exception.
- [ ] A row with `confidence IS NULL` returns `confidence is None` (not coerced).

---

## T2 — Text renderers `format_table` / `format_detail` (S)

**What it delivers:** the human-readable list and the one-run detail view, including the
empty state and the "no diagnosis" rendering for `llm_error`/`no_failed_job` rows.

**Blocked by:** T1.

**RED acceptance criteria (tests in `tests/test_journal_view.py`):**

- [ ] `format_table([])` returns exactly `"No diagnostics recorded."` (trailing newline).
- [ ] `format_table(rows)` contains a header line with `RUN_ID` and `STATUS`, and one line per
      row; `confidence` appears as `0.90` for `0.9` and `-` for `None`.
- [ ] A `llm_error` row (cause/fix/evidence `NULL`) renders without raising, showing `-` for
      `cause`, `confidence` and `fix`.
- [ ] `format_detail(row)` prints every field with its label and lists each evidence line
      prefixed with `  - `.
- [ ] `format_detail` of a row with empty evidence prints no bullet lines.

---

## T3 — HTML renderer `render_html` (M)

**What it delivers:** the self-contained static page — the literal "UI" — with inline CSS,
zero JS, per-row `<details>` evidence, and full HTML escaping of untrusted fields.

**Blocked by:** T1.

**RED acceptance criteria (tests in `tests/test_journal_view.py`):**

- [ ] `render_html(rows)` starts with `<!doctype html>` and ends with `</html>`.
- [ ] The output contains `<style>` and does **not** contain `<script` (case-insensitive).
- [ ] The output contains no external asset: no `src="http`, `href="http`, `<link`, `<img`.
- [ ] A `cause` of `<script>alert(1)</script>` appears as `&lt;script&gt;` and never as the raw
      tag (use `self.assertNotIn("<script>alert", html)` and `assertIn("&lt;script&gt;", html)`).
- [ ] Evidence lines appear inside `<details>` elements (`assertIn("<details>", html)`).
- [ ] `render_html([])` still returns a well-formed page containing `No diagnostics recorded.`
      and `</html>`.
- [ ] An accented value (`échec`) is present verbatim (UTF-8 preserved).

---

## T4 — CLI subcommand `journal` + backward compatibility (M)

**What it delivers:** `python -m pipeline_doctor journal …` wired into `cli.py` with `--db`,
`--repo`, `--status`, `--limit`, `--html`, `--out`, and `show <run_id>` — while
`python -m pipeline_doctor <logfile>` keeps working unchanged.

**Blocked by:** T2, T3.

**RED acceptance criteria (tests in `tests/test_cli.py`, using `redirect_stdout` + temp DB):**

- [ ] `main(["examples/maven-compile-error.log", "--dry-run"])` still routes to the legacy
      diagnostic path (no `journal` output; assert the sanitizer/cleaned-log behavior).
- [ ] `main(["journal", "--db", db_path])` on an empty DB prints `No diagnostics recorded.` and
      returns 0.
- [ ] `main(["journal", "--db", db_path])` on a seeded DB prints a table containing the expected
      `run_id` and `cause`.
- [ ] `main(["journal", "show", str(run_id), "--db", db_path])` prints the cause and the
      evidence lines.
- [ ] `main(["journal", "show", "999999", "--db", db_path])` returns exit code 1 and writes a
      message to stderr.
- [ ] `main(["journal", "--db", db_path, "--html"])` emits output starting with `<!doctype html>`.
- [ ] `main(["journal", "--db", db_path, "--html", "--out", out_path])` writes the page to
      `out_path` (file exists, starts with `<!doctype html>`).
- [ ] `--out` without `--html`, and `--limit 0`, are rejected (argparse, exit code 2).
- [ ] A missing `--db` file behaves as empty (exit 0), not as a crash.

---

## T5 — Integration pass + README update (S)

**What it delivers:** end-to-end confidence that a seeded journal is fully readable through the
real CLI across both formats, plus user-facing documentation.

**Blocked by:** T4.

**RED acceptance criteria (tests in `tests/test_cli.py` + docs):**

- [ ] Seed a DB with one `diagnosed` row and one `llm_error` row; `main(["journal", ...])`
      lists both, and `main(["journal", "--status", "llm_error", ...])` lists only the latter.
- [ ] `main(["journal", "show", ...])` on the `llm_error` run prints `cause: -` and does not
      raise.
- [ ] README "Utilisation" section documents the `journal`, `journal show`, and `journal --html`
      commands; roadmap item 5 is ticked as done for the journal part (demo video/article stay
      unchecked).

---

## Order and edges

```
T1 ─┬─ T2 ─┬─ T4 ─ T5
    └─ T3 ─┘
```

T1 is the shared query layer; T2 and T3 are independent renderers on top of it; T4 wires them
into the CLI; T5 is the integration/doc finish. T1 is the only ticket with no blockers.
