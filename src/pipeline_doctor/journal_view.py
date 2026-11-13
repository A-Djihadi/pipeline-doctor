"""Couche de lecture seule du journal des diagnostics (stdlib uniquement).

Ce module ne fait que CONSULTER la base ecrite par ``journal.py`` : il ne cree,
ne migre et ne modifie jamais la table ``diagnostics``.
"""
import json
import os
import sqlite3

_COLUMNS = "run_id, repo, workflow, branch, cause, confidence, fix, evidence, status, processed_at"
_FIELDS = ["run_id", "repo", "workflow", "branch", "cause", "confidence",
           "fix", "evidence", "status", "processed_at"]


def _decode_evidence(raw):
    """Decode la colonne evidence (JSON) en liste ; [] si absente ou malformee."""
    if raw is None:
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _row_to_dict(row):
    d = dict(zip(_FIELDS, row))
    d["evidence"] = _decode_evidence(d.get("evidence"))
    return d


def list_diagnostics(conn, *, repo=None, status=None, limit=20):
    """Liste les diagnostics, du plus recent au plus ancien. Filtres exacts optionnels."""
    rows = conn.execute(
        f"""
        SELECT {_COLUMNS}
        FROM diagnostics
        WHERE (?1 IS NULL OR repo = ?1)
          AND (?2 IS NULL OR status = ?2)
        ORDER BY processed_at DESC, run_id DESC
        LIMIT ?3
        """,
        (repo, status, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_diagnostic(conn, run_id):
    """Retourne le diagnostic d'un run sous forme de dict, ou None si absent."""
    row = conn.execute(
        f"""
        SELECT {_COLUMNS}
        FROM diagnostics
        WHERE run_id = ?1
        """,
        (run_id,),
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def open_readonly(path):
    """Ouvre une base existante en lecture seule ; None si le fichier est absent."""
    if not os.path.exists(path):
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
