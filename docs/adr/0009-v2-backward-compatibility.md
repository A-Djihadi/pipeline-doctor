# ADR-0009 : v2 backward compatibility — opt-in flags, additive journal schema, frozen v1 paths

- Status: proposed
- Date: 2026-11-13

## Context

v2 adds a Jenkins source (ADR-0007) and fix proposals / draft PRs (ADR-0008). Three paths are
in production use and must keep working byte-for-byte for an operator who upgrades without
changing configuration:

1. the CLI `python -m pipeline_doctor <logfile> [--dry-run] [--max-chars N]`;
2. the `diagnose.yml` workflow (`workflow_run` → `notify.py` → Slack, no LLM, ADR-0002);
3. the self-hosted agent `python -m pipeline_doctor.agent` (GitHub poll → Ollama/graph → Slack →
   journal, ADR-0003/0005), whose idempotence lives in the SQLite table `diagnostics`.

The journal is the riskiest piece: a broken migration would make the agent forget which runs it
already processed and re-notify every recent failure.

## Decision

1. **Everything new is off by default.** `PD_SOURCES` defaults to `github`; `PD_FIX_MODE`
   defaults to `off`. With neither set, the agent's behaviour, messages, exit codes and Slack
   payloads are those of v1.
2. **Frozen v1 surfaces.** `cli.py`, `notify.py`, `sanitizer.py`, `slack.build_payload`,
   `slack.build_diagnosis_payload`, `.github/workflows/*` and the `diagnostics` table schema are
   not modified by v2. `agent.poll_once(repo, token, workflow, db_path, webhook, dry_run)` keeps
   its signature; fix handling is an optional keyword argument defaulting to "off".
3. **Characterization tests are the contract.** The existing `tests/test_agent.py`,
   `test_notify.py`, `test_sanitizer.py`, `test_journal.py`, `test_github.py` must pass
   **unmodified**. Names they patch (`agent.list_runs`, `agent.list_jobs`, `agent.job_log`,
   `agent.ask_ollama`, `agent.send`) stay importable from `agent`.
4. **Additive-only journal schema.** No `ALTER`, no table rebuild, no data migration.
   `journal.init()` additionally runs `CREATE TABLE IF NOT EXISTS` for two new tables:
   `jenkins_diagnostics` (key: canonical build URL) and `fix_proposals` (unique
   `(source, source_ref)`). Opening a v1 database with v2 code leaves every existing row intact;
   opening it again with v1 code still works (v1 ignores unknown tables).
5. **Sources are isolated.** A failure of one source (Jenkins unreachable, bad credentials) is
   reported on stderr and does not prevent the other source from being polled in the same pass.
   A failure of the fix flow never changes the diagnosis status, the diagnosis Slack message, or
   the `diagnostics` row — the diagnosis is recorded before the fix flow starts.
6. **Small, additive API changes allowed**, each with a default that preserves v1 behaviour:
   `llm.chat(..., user_template=...)` (default = the current "Log du build en echec" prefix) and
   moving `grounding()` from `graph.py` to `rules.py` (stdlib) with `graph.grounding` re-exported.
7. **Configuration errors fail fast with exit code 2**, as today: unknown `PD_SOURCES` entry,
   unknown `PD_FIX_MODE`, missing Jenkins variables when `jenkins` is enabled, missing
   `PD_FIX_TOKEN`/`PD_FIX_FORK` or `PD_FIX_TOKEN == GITHUB_TOKEN` in `pr` mode. With the defaults,
   the only check remains "PD_REPO and GITHUB_TOKEN" with the v1 message.

## Consequences

- Upgrading is a no-op until an operator sets a v2 variable; rolling back to v1 code is safe on a
  database written by v2.
- Two parallel tables (`diagnostics`, `jenkins_diagnostics`) have near-identical columns. This
  duplication is accepted to keep the idempotence table untouched; unifying them (e.g. a
  `run_key` column with a tested migration and backup) can be decided later in its own ADR.
- The ADR-0006 viewer, when implemented, shows GitHub diagnostics only until extended.
- `diagnose.yml` remains the no-LLM fallback for GitHub; there is no Jenkins equivalent.
