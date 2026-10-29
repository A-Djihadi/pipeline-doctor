"""Usage : python -m pipeline_doctor examples/maven-compile-error.log"""
import argparse
import sys

from .llm import OLLAMA_MODEL, ask_ollama
from .sanitizer import sanitize


def main(argv=None) -> int:
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
