"""Agent auto-heberge : scrute les runs CI en echec, diagnostique (Ollama), notifie Slack.

Usage :
    python -m pipeline_doctor.agent --once              # un seul passage (cron)
    python -m pipeline_doctor.agent --interval 300      # boucle (service systemd)
    python -m pipeline_doctor.agent --once --dry-run    # sans envoyer ni journaliser

Variables d'environnement :
    PD_REPO        owner/repo a surveiller (ex. A-Djihadi/pipeline-doctor)
    PD_WORKFLOW    nom de fichier du workflow (defaut ci.yml)
    GITHUB_TOKEN   token lecture seule (Actions: read) pour lister runs et lire logs
    PD_DB          chemin du journal SQLite (defaut diagnostics.db)
    OLLAMA_URL, OLLAMA_MODEL, SLACK_WEBHOOK_URL (voir .env.example)

Politique d'idempotence : un run est traite une seule fois (journal). Si le
diagnostic LLM echoue, on notifie quand meme avec « diagnostic indisponible »
et on journalise `llm_error` ; si l'envoi Slack echoue, statut `notify_error`.
"""
import argparse
import json
import os
import sys
import time

from .github import failed_job_ids, job_log, list_jobs, list_runs, run_url
from .journal import init as init_journal
from .journal import is_processed, record
from .llm import ask_ollama
from .sanitizer import sanitize
from .slack import build_diagnosis_payload, send

MAX_CHARS = 12000


def poll_once(repo, token, workflow, db_path, webhook, dry_run=False):
    """Un passage : traite les runs en echec non encore vus. Retourne le nombre traite."""
    conn = init_journal(db_path)
    processed = 0
    try:
        for run in list_runs(repo, workflow, token):
            run_id = run.get("id")
            if run.get("conclusion") != "failure":
                continue
            if is_processed(conn, run_id):
                continue

            jobs = list_jobs(repo, str(run_id), token)
            failed = failed_job_ids(jobs)
            branch = run.get("head_branch") or "-"

            if not failed:
                record(conn, run_id, repo, workflow, branch, None, "no_failed_job")
                print(f"[agent] run {run_id} : aucun job en echec, marque.", file=sys.stderr)
                continue

            raw = "\n\n".join(job_log(repo, str(jid), token) for jid in failed)
            cleaned, n_redacted = sanitize(raw, MAX_CHARS)

            diagnosis = None
            status = "diagnosed"
            try:
                diagnosis = ask_ollama(cleaned)
            except Exception as exc:  # Ollama injoignable ou JSON invalide
                print(f"[agent] diagnostic LLM impossible (run {run_id}) : {exc}", file=sys.stderr)
                status = "llm_error"

            payload = build_diagnosis_payload(
                repo, run.get("name") or "CI", branch, run_id,
                run_url(repo, str(run_id)), n_redacted, diagnosis, cleaned,
            )

            if dry_run:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                processed += 1
                continue

            if webhook:
                try:
                    send(webhook, payload)
                except Exception as exc:
                    print(f"[agent] envoi Slack impossible (run {run_id}) : {exc}", file=sys.stderr)
                    status = "notify_error"
            else:
                print("SLACK_WEBHOOK_URL absent : notification ignoree.", file=sys.stderr)

            record(conn, run_id, repo, workflow, branch, diagnosis, status)
            processed += 1
            print(f"[agent] run {run_id} traite (status={status}).", file=sys.stderr)
    finally:
        conn.close()
    return processed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline-doctor-agent")
    parser.add_argument("--once", action="store_true", help="un seul passage puis sortie")
    parser.add_argument("--interval", type=int, default=0, help="boucle infinie, pause en secondes")
    parser.add_argument("--dry-run", action="store_true", help="n'envoie rien et ne journalise rien")
    args = parser.parse_args(argv)

    repo = os.environ.get("PD_REPO") or os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        print("Variables manquantes : PD_REPO et GITHUB_TOKEN.", file=sys.stderr)
        return 2

    workflow = os.environ.get("PD_WORKFLOW", "ci.yml")
    db_path = os.environ.get("PD_DB", "diagnostics.db")
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")

    if args.interval > 0:
        print(f"[agent] boucle : un passage toutes les {args.interval}s (Ctrl-C pour arreter).", file=sys.stderr)
        try:
            while True:
                poll_once(repo, token, workflow, db_path, webhook, dry_run=args.dry_run)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0

    poll_once(repo, token, workflow, db_path, webhook, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
