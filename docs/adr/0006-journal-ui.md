# ADR-0006 : Diagnostic journal UI — read-only CLI subcommand + static HTML export

- Status: accepted
- Date: 2026-11-10

## Context

Session 3 introduced the SQLite journal (`journal.py`, table `diagnostics`) to make the agent
idempotent and to keep the diagnostic history. The data is written today, but there is no
supported way to *consult* it: a user has to open `diagnostics.db` with the `sqlite3` shell by
hand. The roadmap (README, item 5) promises a "journal des diagnostics (UI)".

Two hard constraints from earlier decisions bound the design:

- **ADR-0001 — zero dependency**: standard library only for the core. Flask/FastAPI and any
  heavyweight server are out.
- **ADR-0001 — read-only**: the agent *proposes*, it never applies anything. The journal viewer
  must be pure read; it must never mutate, re-diagnose, or re-notify.

## Options considered

1. **CLI subcommand `journal`** — `journal` (list), `journal show <run_id>`, filters
   (`--repo`, `--status`, `--limit`). Read-only, no dependency, scriptable, works over SSH.
2. **Static HTML export** — `journal --html` writes a self-contained page (inline CSS, zero JS,
   no external asset) that opens in any browser. The literal "UI", produced without a server.
3. **Mini-server `http.server` stdlib dashboard** — a read-only local HTTP dashboard. Possible
   in stdlib, but heavier.

## Decision

1. **CLI subcommand `journal`** is the primary interface (option 1). `python -m pipeline_doctor
   journal` lists recent diagnostics; `journal show <run_id>` prints the full detail. Filters
   `--repo`, `--status`, `--limit`, and a `--db` override select what to read. Read-only.
2. **Static HTML export** is the visual UI (option 2). `journal --html` (optionally
   `--out <file>`) renders the filtered list to one self-contained HTML file — inline CSS,
   zero JavaScript, no external stylesheet or font — openable in a browser or attachable to a
   ticket/PR. This is what satisfies the "UI" in the roadmap item; it needs no server.
3. **The mini-server is rejected** (option 3). A `http.server` dashboard would require a
   long-running process, port selection, a bind decision, and it exposes the journal over the
   network by default — all to deliver a view that the static export already delivers as a
   shareable, archivable file. No benefit justifies that operational surface. The "no server"
   spirit of ADR-0001 wins; the dashboard is not implemented and this ADR closes the question.

The combination covers two distinct jobs with one shared read-only query layer: quick,
scriptable consultation over SSH (CLI), and a real visual view that can be opened, shared, or
used as the subject of the later demo video (HTML export).

## Read schema

The viewer reads only. It opens an *existing* database read-only:

```python
sqlite3.connect(f"file:{path}?mode=ro", uri=True)
```

If the file does not exist, the viewer treats it as an empty journal and prints the empty-state
message — it never creates a database file.

### List query

```sql
SELECT run_id, repo, workflow, branch, cause, confidence, fix, evidence, status, processed_at
FROM diagnostics
WHERE (?1 IS NULL OR repo = ?1)
  AND (?2 IS NULL OR status = ?2)
ORDER BY processed_at DESC, run_id DESC
LIMIT ?3
```

`?1` = repo filter (`None` = no filter, exact match), `?2` = status filter (`None` = no filter,
exact match), `?3` = limit (positive integer).

### Show query

```sql
SELECT run_id, repo, workflow, branch, cause, confidence, fix, evidence, status, processed_at
FROM diagnostics
WHERE run_id = ?1
```

### Evidence decoding

`evidence` is stored as a JSON list of strings (TEXT). The viewer decodes it with
`json.loads`, wrapped so that a missing or malformed value yields `[]` rather than a crash
(defensive against legacy or corrupt rows).

## Consequences

- A new read-only module (`journal_view.py`) owns the queries and both renderers (text and
  HTML). `cli.py` gains a `journal` subcommand dispatched *before* the legacy positional
  `logfile` path, so `python -m pipeline_doctor <logfile>` keeps working unchanged.
- The journal stays write-only-by-the-agent; the viewer is pure read. A `sqlite3` file locked
  or missing is handled as empty/error, never as a reason to write.
- `status` remains an opaque string defined by the writer (agent): `diagnosed`, `llm_error`,
  `notify_error`, `no_failed_job`, `rules_fallback`. The viewer displays and filters it, never
  validates an enum.
- No dependency is added; the stdlib-only CI (`unittest discover`) stays green.
