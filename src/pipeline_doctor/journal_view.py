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


_TABLE_HEADERS = ["RUN_ID", "STATUS", "CONF", "REPO", "WORKFLOW", "BRANCH", "CAUSE"]
_CAUSE_WIDTH = 60


def _fmt_confidence(value):
    return f"{value:.2f}" if value is not None else "-"


def _val(value):
    return "-" if value is None or value == "" else str(value)


def _fmt_cause(cause):
    if cause is None:
        return "-"
    text = str(cause).replace("\r", " ").replace("\n", " ")
    if len(text) > _CAUSE_WIDTH:
        return text[:_CAUSE_WIDTH] + "\u2026"
    return text


def _table_cells(row):
    return [
        str(row["run_id"]),
        str(row["status"]),
        _fmt_confidence(row["confidence"]),
        str(row["repo"]),
        _val(row["workflow"]),
        _val(row["branch"]),
        _fmt_cause(row["cause"]),
    ]


def format_table(rows):
    """Rend la liste en tableau texte aligne, colonnes RUN_ID..CAUSE."""
    if not rows:
        return "No diagnostics recorded.\n"
    rendered = [_table_cells(row) for row in rows]
    widths = [len(h) for h in _TABLE_HEADERS]
    for cells in rendered:
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{{i}:<{widths[i]}}}" for i in range(len(_TABLE_HEADERS)))
    lines = [fmt.format(*_TABLE_HEADERS)]
    lines.extend(fmt.format(*cells) for cells in rendered)
    return "\n".join(lines) + "\n"


_DETAIL_LABELS = [
    ("run_id", lambda r: str(r["run_id"])),
    ("repo", lambda r: _val(r["repo"])),
    ("workflow", lambda r: _val(r["workflow"])),
    ("branch", lambda r: _val(r["branch"])),
    ("status", lambda r: _val(r["status"])),
    ("cause", lambda r: _val(r["cause"])),
    ("confidence", lambda r: _fmt_confidence(r["confidence"])),
    ("fix", lambda r: _val(r["fix"])),
    ("processed_at", lambda r: _val(r["processed_at"])),
]
_LABEL_WIDTH = max(len(label) for label, _ in _DETAIL_LABELS)


def format_detail(row):
    """Rend un diagnostic en lignes etiquetees + liste a puces des preuves."""
    lines = [f"{label:<{_LABEL_WIDTH}}: {render(row)}" for label, render in _DETAIL_LABELS]
    lines.append("evidence:")
    for line in row["evidence"] or []:
        lines.append(f"  - {line}")
    return "\n".join(lines) + "\n"
