# Coder brief — session 5 (journal des diagnostics, UI)

Implement the design from ADR-0006 and the spec. This is a **read-only** feature: you add a way
to *consult* the diagnostic history; you never change how diagnoses are produced or stored.

Read first (in order): `docs/adr/0006-journal-ui.md`, then
`docs/superpowers/specs/pipeline-doctor-journal.md`, then `docs/tickets-session5.md`.

## Ground rules

- **Zero dependency** (ADR-0001): `sqlite3`, `argparse`, `json`, `html`, `os` only. No new
  package, no `pip install`.
- **Read-only**: the viewer must never write to, create, or migrate the database. Open with
  `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`; a missing file is an *empty journal*, not
  a reason to create one.
- **Backward compatible**: `python -m pipeline_doctor <logfile>` and its `--max-chars` /
  `--dry-run` flags must behave exactly as today. The `journal` feature is purely additive.
- **Test-first**: write the RED tests from `docs/tickets-session5.md` before the implementation.
  Keep the whole suite green under `PYTHONPATH=src python -m unittest discover -s tests -v`.

## Implementation order

Work the tickets in order: T1 → T2/T3 → T4 → T5. Commit per ticket (English messages, e.g.
`feat: read-only journal query layer`).

### T1 — `src/pipeline_doctor/journal_view.py` (new)

Read-only query functions, each taking an already-open `sqlite3.Connection`:

- `list_diagnostics(conn, *, repo=None, status=None, limit=20) -> list[dict]`
- `get_diagnostic(conn, run_id) -> dict | None`
- `open_readonly(path) -> sqlite3.Connection | None`

Use the exact SQL from ADR-0006 (parameterized; `None` means "no filter"). Return a dict per row
with `evidence` decoded via `json.loads` inside a `try/except` that yields `[]`. Do **not** put
rendering logic here — keep it a pure data-access module.

### T2 — text renderers (same module)

- `format_table(rows) -> str` — aligned columns `RUN_ID STATUS CONF REPO WORKFLOW BRANCH CAUSE`,
  confidence `0.90` / `-`, cause truncated to one line (~60 chars + `…`).
- `format_detail(row) -> str` — labelled lines + `  - ` evidence bullets, `-` for empty fields.
- `format_table([]) -> "No diagnostics recorded.\n"`.

Use `str.format`; no third-party table library.

### T3 — HTML renderer (same module)

- `render_html(rows, *, db_path="") -> str` — a single self-contained page: `<!doctype html>`,
  `<meta charset="utf-8">`, inline `<style>`, **no** `<script>`, **no** external assets.
- Escape **every** field with `html.escape`. Evidence goes in `<details>` per row. Confidence
  `-` when `None`. Empty list still yields a well-formed page with `No diagnostics recorded.`.

### T4 — `src/pipeline_doctor/cli.py` (extend)

- Keep the existing diagnostic parser exactly as-is for the legacy path.
- Dispatch on the first argument: if `argv and argv[0] == "journal"`, build a separate
  `argparse` parser for the journal (subcommands `show`, plus the list flags `--db`, `--repo`,
  `--status`, `--limit` (positive int, default 20), `--html`, `--out`), and run it; otherwise
  run the legacy path. This preserves `pipeline_doctor <logfile>` untouched.
- DB default: `os.environ.get("PD_DB", "diagnostics.db")` (identical to `agent.py`).
- Wire `open_readonly` → query → renderer → `print`. Map exit codes per the spec
  (0 success/empty, 1 `show` not found, 2 usage or unreadable DB).

### T5 — `tests/test_cli.py` (new) + `README.md` (update)

Integration tests seeding a temp DB (see fixtures below) and asserting stdout/stderr and exit
codes via `contextlib.redirect_stdout` / `redirect_stderr`. Update README "Utilisation" with the
three commands and tick roadmap item 5 (journal only; leave demo video and article unchecked).

## Test fixtures

Follow the existing convention (`tests/test_journal.py`, `tests/test_agent.py`):

```python
conn = journal.init(":memory:")                 # in-memory, schema auto-created
journal.record(conn, 101, "A-Djihadi/pipeline-doctor", "ci.yml", "main",
               {"cause": "x", "confidence": 0.9, "fix": "f", "evidence": ["e1"]},
               "diagnosed")
journal.record(conn, 102, "A-Djihadi/pipeline-doctor", "ci.yml", "main", None, "llm_error")
```

Pass the same `conn` into `journal_view` functions. For CLI tests use a `tempfile.TemporaryDirectory()`
DB path so `open_readonly` exercises the real file path (or a missing-file path for the empty case).

## Files touched

- `src/pipeline_doctor/journal_view.py` — **new**, all of T1–T3.
- `src/pipeline_doctor/cli.py` — T4 (add the `journal` dispatch; do not alter the legacy path).
- `tests/test_journal_view.py` — **new**, T1–T3 RED tests.
- `tests/test_cli.py` — **new**, T4–T5 RED tests.
- `README.md` — T5 (Usage + roadmap).

Do **not** touch `journal.py`, `agent.py`, `llm.py`, or the graph/evals/tracing modules. Do not
add dependencies. Do not modify the `diagnostics` table schema.
