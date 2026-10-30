"""Recupere le log des jobs en echec, le nettoie, notifie Slack.

Usage (dans GitHub Actions) :
    python -m pipeline_doctor.notify [--dry-run]

Variables d'environnement :
    GITHUB_REPOSITORY  (fournie par Actions)
    GITHUB_TOKEN       (token du job, portee actions:read)
    PD_RUN_ID          (id du run a diagnostiquer)
    PD_RUN_NAME        (nom du workflow, cosmetique)
    PD_BRANCH          (branche, cosmetique)
    SLACK_WEBHOOK_URL  (optionnel : si absent, on affiche le payload sans poster)

Sans LLM : la session 2 notifie seulement (log nettoye + lien). Le diagnostic
par modele arrive en session 3.
"""
import argparse
import json
import os
import sys

from .github import failed_job_ids, job_log, list_jobs, run_url
from .sanitizer import sanitize
from .slack import build_payload, send

MAX_CHARS = 12000


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline-doctor-notify")
    parser.add_argument("--dry-run", action="store_true", help="affiche le payload sans poster")
    args = parser.parse_args(argv)

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    run_id = os.environ.get("PD_RUN_ID", "")

    if not (repo and token and run_id):
        print(
            "Variables manquantes : GITHUB_REPOSITORY, GITHUB_TOKEN, PD_RUN_ID.",
            file=sys.stderr,
        )
        return 2

    jobs = list_jobs(repo, run_id, token)
    failed = failed_job_ids(jobs)
    if not failed:
        print("Aucun job en echec dans ce run : notification ignoree.")
        return 0

    raw = "\n\n".join(job_log(repo, str(jid), token) for jid in failed)
    cleaned, n_redacted = sanitize(raw, MAX_CHARS)
    print(
        f"[sanitizer] {len(failed)} job(s) en echec, "
        f"{n_redacted} secret(s) masque(s), {len(cleaned)} caracteres.",
        file=sys.stderr,
    )

    run_name = os.environ.get("PD_RUN_NAME") or "CI"
    branch = os.environ.get("PD_BRANCH") or "-"
    url = run_url(repo, run_id)
    payload = build_payload(repo, run_name, branch, run_id, url, n_redacted, cleaned)

    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    if args.dry_run or not webhook:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if not webhook:
            print("SLACK_WEBHOOK_URL absent : notification ignoree.", file=sys.stderr)
        return 0

    send(webhook, payload)
    print(f"Notification Slack envoyee ({len(failed)} job(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
