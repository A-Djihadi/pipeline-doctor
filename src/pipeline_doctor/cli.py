"""Usage : python -m pipeline_doctor examples/maven-compile-error.log"""
import argparse
import os
import sqlite3
import sys

from .journal_view import (format_detail, format_table, get_diagnostic,
                           list_diagnostics, open_readonly, render_html)
from .llm import OLLAMA_MODEL, ask_ollama
from .sanitizer import sanitize


def main(argv=None) -> int:
    args_list = list(sys.argv[1:]) if argv is None else list(argv)
    if args_list and args_list[0] == "journal":
        try:
            return _journal_main(args_list[1:])
        except SystemExit as exc:
            code = exc.code
            return code if isinstance(code, int) else 0
    return _legacy_main(argv)


def _legacy_main(argv):
    parser = argparse.ArgumentParser(prog="pipeline-doctor", description="Diagnostique un log de build CI en echec.")
    parser.add_argument("logfile", help="chemin du log, ou - pour stdin")
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--dry-run", action="store_true", help="affiche le log nettoye sans appeler le modele")
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.logfile == "-" else open(args.logfile, encoding="utf-8", errors="replace").read()
    cleaned, n_redacted = sanitize(raw, args.max_chars)
    print(f"[sanitizer] {n_redacted} secret(s) masque(s), {len(cleaned)} caracteres envoyes", file=sys.stderr)

    if args.dry_run:
        print(cleaned)
        return 0

    print(f"[llm] modele local : {OLLAMA_MODEL}", file=sys.stderr)
    result = ask_ollama(cleaned)

    print(f"\nCause      : {result.get('cause')}")
    print(f"Confiance  : {result.get('confidence')}")
    for line in result.get("evidence", []):
        print(f"  > {line}")
    print(f"Correctif  : {result.get('fix')}")
    return 0


def _positive_int(value):
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError(f"entier positif attendu, recu {value!r}")
    return n


def _build_journal_parser(db_default):
    parser = argparse.ArgumentParser(
        prog="pipeline-doctor journal",
        description="Consulte l'historique des diagnostics (lecture seule).",
    )
    parser.add_argument("--db", default=db_default,
                        help="fichier SQLite a lire (defaut : $PD_DB ou diagnostics.db)")
    sub = parser.add_subparsers(dest="command")

    show = sub.add_parser("show", help="affiche un diagnostic en detail")
    show.add_argument("run_id", type=int, help="identifiant du run")
    show.add_argument("--db", default=argparse.SUPPRESS,
                      help="fichier SQLite a lire (defaut : $PD_DB ou diagnostics.db)")

    parser.add_argument("--repo", help="filtrer sur le depot (correspondance exacte)")
    parser.add_argument("--status", help="filtrer sur le statut (correspondance exacte)")
    parser.add_argument("--limit", type=_positive_int, default=20,
                        help="nombre max de lignes (entier positif, defaut 20)")
    parser.add_argument("--html", action="store_true",
                        help="exporter en page HTML autonome au lieu du tableau texte")
    parser.add_argument("--out", help="ecrire la page HTML dans ce fichier (requiert --html)")
    return parser


def _journal_main(argv):
    db_default = os.environ.get("PD_DB", "diagnostics.db")
    parser = _build_journal_parser(db_default)
    args = parser.parse_args(argv)

    if args.command == "show":
        return _run_show(args)

    if args.out and not args.html:
        parser.error("--out requiert --html")
    return _run_list(args)


def _run_list(args):
    conn = None
    try:
        conn = open_readonly(args.db)
        rows = (list_diagnostics(conn, repo=args.repo, status=args.status, limit=args.limit)
                if conn is not None else [])
    except sqlite3.Error as exc:
        print(f"journal: impossible de lire la base {args.db!r} : {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()

    output = render_html(rows, db_path=args.db) if args.html else format_table(rows)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(output)
    else:
        sys.stdout.write(output)
    return 0


def _run_show(args):
    conn = None
    try:
        conn = open_readonly(args.db)
        row = get_diagnostic(conn, args.run_id) if conn is not None else None
    except sqlite3.Error as exc:
        print(f"journal: impossible de lire la base {args.db!r} : {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()

    if row is None:
        print(f"journal: run {args.run_id} introuvable", file=sys.stderr)
        return 1
    sys.stdout.write(format_detail(row))
    return 0
