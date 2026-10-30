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
