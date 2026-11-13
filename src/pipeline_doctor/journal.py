"""Journal des diagnostics (SQLite, stdlib) : idempotence + historique.

Chaque run CI en echec est traite une seule fois : son id est enregistre,
pour ne jamais re-notifier le meme echec au passage suivant. L'historique
(posologie) servira de base a la session 4 (journal des diagnostics).
"""
import json
import sqlite3
import time


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def init(path: str) -> sqlite3.Connection:
    """Ouvre (et cree si besoin) la base, retourne la connexion."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS diagnostics (
            run_id       INTEGER PRIMARY KEY,
            repo         TEXT NOT NULL,
            workflow     TEXT,
            branch       TEXT,
            cause        TEXT,
            confidence   REAL,
            fix          TEXT,
            evidence     TEXT,
            status       TEXT NOT NULL,
            processed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
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
        )
        """
    )
    conn.execute(
        """
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
        )
        """
    )
    conn.commit()
    return conn


def is_processed(conn: sqlite3.Connection, run_id) -> bool:
    row = conn.execute("SELECT 1 FROM diagnostics WHERE run_id = ?", (run_id,)).fetchone()
    return row is not None


def record(conn: sqlite3.Connection, run_id, repo, workflow, branch, diagnosis, status):
    """Enregistre (ou remplace) l'entree d'un run. diagnosis peut etre None."""
    d = diagnosis or {}
    conn.execute(
        """
        INSERT OR REPLACE INTO diagnostics
            (run_id, repo, workflow, branch, cause, confidence, fix, evidence, status, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            repo,
            workflow,
            branch,
            d.get("cause"),
            d.get("confidence"),
            d.get("fix"),
            json.dumps(d.get("evidence", [])),
            status,
            _now(),
        ),
    )
    conn.commit()


def is_build_processed(conn: sqlite3.Connection, build_key) -> bool:
    row = conn.execute(
        "SELECT 1 FROM jenkins_diagnostics WHERE build_key = ?", (build_key,)
    ).fetchone()
    return row is not None


def record_build(conn: sqlite3.Connection, build_key, instance, job, number, diagnosis, status):
    """Enregistre (ou remplace) l'entree d'un build Jenkins. diagnosis peut etre None."""
    d = diagnosis or {}
    conn.execute(
        """
        INSERT OR REPLACE INTO jenkins_diagnostics
            (build_key, instance, job, number, cause, confidence, fix, evidence, status, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            build_key,
            instance,
            job,
            number,
            d.get("cause"),
            d.get("confidence"),
            d.get("fix"),
            json.dumps(d.get("evidence", [])),
            status,
            _now(),
        ),
    )
    conn.commit()


def record_fix(conn: sqlite3.Connection, row: dict):
    """Enregistre (ou remplace) une proposition de correctif. `created_at` est pose ici."""
    files = row.get("files")
    if isinstance(files, list):
        files = json.dumps(files)
    conn.execute(
        """
        INSERT OR REPLACE INTO fix_proposals
            (source, source_ref, repo, base_branch, base_sha, head_branch,
             status, reason, files, diff, pr_number, pr_url, model, prompt_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row.get("source"),
            row.get("source_ref"),
            row.get("repo"),
            row.get("base_branch"),
            row.get("base_sha"),
            row.get("head_branch"),
            row.get("status"),
            row.get("reason"),
            files,
            row.get("diff"),
            row.get("pr_number"),
            row.get("pr_url"),
            row.get("model"),
            row.get("prompt_version"),
            _now(),
        ),
    )
    conn.commit()


def fixes_since(conn: sqlite3.Connection, repo, since_iso) -> int:
    """Nombre de propositions `proposed`/`pr_opened` du repo depuis `since_iso`."""
    row = conn.execute(
        "SELECT COUNT(*) FROM fix_proposals "
        "WHERE repo = ? AND status IN ('proposed', 'pr_opened') AND created_at >= ?",
        (repo, since_iso),
    ).fetchone()
    return row[0]
