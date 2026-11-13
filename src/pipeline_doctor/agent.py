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
    PD_ENGINE      "linear" (defaut, stdlib) ou "graph" (LangGraph + Langfuse, ADR-0005)
    PD_SOURCES     "github" (defaut), "jenkins", ou "github,jenkins"
    PD_JENKINS_URL / PD_JENKINS_JOBS / PD_JENKINS_USER / PD_JENKINS_TOKEN
                   (requis si "jenkins" est active ; voir .env.example)
    PD_JENKINS_PER_JOB / PD_JENKINS_MAX_LOG_BYTES (defauts 10 / 2 MiB)

Politique d'idempotence : un run (GitHub) ou un build (Jenkins) est traite une seule
fois (journal). Si le diagnostic LLM echoue, on notifie quand meme avec « diagnostic
indisponible » et on journalise `llm_error` ; si l'envoi Slack echoue, statut
`notify_error`. Les sources sont isolees : l'echec de l'une n'empeche pas l'autre.
"""
import argparse
import json
import os
import sys
import time

from .github import failed_job_ids, job_log, list_jobs, list_runs, run_url
from .jenkins import JenkinsError, build_url, check_base_url, console_log, list_failed_builds
from .journal import init as init_journal
from .journal import is_build_processed, is_processed, record, record_build
from .llm import ask_ollama
from .sanitizer import sanitize
from .slack import build_diagnosis_payload, send

MAX_CHARS = 12000
KNOWN_SOURCES = ("github", "jenkins")


def _diagnose(raw, cleaned, session_id, metadata):
    """Selection du moteur (PD_ENGINE). Retourne (diagnosis, status, source).

    `source` vaut "llm" (lineaire reussi), "rules" (repli du graphe) ou "" (erreur LLM).
    """
    diagnosis = None
    status = "diagnosed"
    source = ""
    if os.environ.get("PD_ENGINE") == "graph":
        from .graph import run as run_graph  # extra optionnel
        result = run_graph(raw, session_id=session_id, metadata=metadata)
        diagnosis = result.get("diagnosis")
        source = result.get("source") or ""
        if source == "rules":
            status = "rules_fallback"
    else:
        try:
            diagnosis = ask_ollama(cleaned)
            source = "llm"
        except Exception as exc:  # Ollama injoignable ou JSON invalide
            print(f"[agent] diagnostic LLM impossible : {exc}", file=sys.stderr)
            status = "llm_error"
    return diagnosis, status, source


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

            diagnosis, status, _ = _diagnose(
                raw, cleaned, session_id=f"{repo}#{run_id}",
                metadata={"repo": repo, "run_id": run_id, "branch": branch},
            )

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


def poll_jenkins_once(base, jobs, user, token, db_path, webhook, dry_run=False,
                      per_job=10, max_log_bytes=2_097_152) -> int:
    """Un passage Jenkins : diagnostique les builds FAILURE non encore vus."""
    conn = init_journal(db_path)
    processed = 0
    try:
        for job in jobs:
            try:
                failed = list_failed_builds(base, job, user, token, per_job=per_job)
            except Exception as exc:
                print(f"[agent] Jenkins indisponible (job {job}) : {exc}", file=sys.stderr)
                continue
            for number in failed:
                key = build_url(base, job, number)
                if is_build_processed(conn, key):
                    continue
                try:
                    raw = console_log(base, job, number, user, token,
                                      max_tail_bytes=max_log_bytes)
                except Exception as exc:
                    print(f"[agent] console Jenkins impossible (job {job} build {number}) : {exc}",
                          file=sys.stderr)
                    continue
                cleaned, n_redacted = sanitize(raw, MAX_CHARS)

                diagnosis, status, _ = _diagnose(
                    raw, cleaned, session_id=key,
                    metadata={"source": "jenkins", "job": job, "build": number},
                )

                payload = build_diagnosis_payload(
                    f"jenkins:{job}", "Jenkins", "-", number, key,
                    n_redacted, diagnosis, cleaned,
                )

                if dry_run:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    processed += 1
                    continue

                if webhook:
                    try:
                        send(webhook, payload)
                    except Exception as exc:
                        print(f"[agent] envoi Slack impossible (job {job} build {number}) : {exc}",
                              file=sys.stderr)
                        status = "notify_error"
                else:
                    print("SLACK_WEBHOOK_URL absent : notification ignoree.", file=sys.stderr)

                record_build(conn, key, base, job, number, diagnosis, status)
                processed += 1
                print(f"[agent] build Jenkins {key} traite (status={status}).", file=sys.stderr)
    finally:
        conn.close()
    return processed


def _parse_sources(raw: str):
    """Decoupe PD_SOURCES en liste de sources valides ; None si une source est inconnue."""
    sources = []
    for name in raw.split(","):
        name = name.strip()
        if not name:
            continue
        if name not in KNOWN_SOURCES:
            print(f"PD_SOURCES invalide : source inconnue « {name} ».", file=sys.stderr)
            return None
        sources.append(name)
    return sources


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline-doctor-agent")
    parser.add_argument("--once", action="store_true", help="un seul passage puis sortie")
    parser.add_argument("--interval", type=int, default=0, help="boucle infinie, pause en secondes")
    parser.add_argument("--dry-run", action="store_true", help="n'envoie rien et ne journalise rien")
    args = parser.parse_args(argv)

    sources = _parse_sources(os.environ.get("PD_SOURCES", "github"))
    if sources is None:
        return 2

    # Validation par source (echec rapide ; message v1 strictement identique pour github).
    repo = os.environ.get("PD_REPO") or os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if "github" in sources and (not repo or not token):
        print("Variables manquantes : PD_REPO et GITHUB_TOKEN.", file=sys.stderr)
        return 2

    jenkins_cfg = None
    if "jenkins" in sources:
        missing = [name for name in
                   ("PD_JENKINS_URL", "PD_JENKINS_JOBS", "PD_JENKINS_USER", "PD_JENKINS_TOKEN")
                   if not os.environ.get(name)]
        if missing:
            print(f"Variables manquantes : {', '.join(missing)}.", file=sys.stderr)
            return 2
        try:
            base = check_base_url(os.environ.get("PD_JENKINS_URL", ""))
        except JenkinsError as exc:
            print(f"{exc}", file=sys.stderr)
            return 2
        jenkins_cfg = {
            "base": base,
            "jobs": [j.strip() for j in os.environ.get("PD_JENKINS_JOBS", "").split(",")
                     if j.strip()],
            "user": os.environ.get("PD_JENKINS_USER", ""),
            "token": os.environ.get("PD_JENKINS_TOKEN", ""),
            "per_job": int(os.environ.get("PD_JENKINS_PER_JOB", "10")),
            "max_log_bytes": int(os.environ.get("PD_JENKINS_MAX_LOG_BYTES", "2097152")),
        }

    workflow = os.environ.get("PD_WORKFLOW", "ci.yml")
    db_path = os.environ.get("PD_DB", "diagnostics.db")
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")

    def _pass():
        for source in sources:
            try:
                if source == "github":
                    poll_once(repo, token, workflow, db_path, webhook, dry_run=args.dry_run)
                elif source == "jenkins":
                    poll_jenkins_once(jenkins_cfg["base"], jenkins_cfg["jobs"],
                                      jenkins_cfg["user"], jenkins_cfg["token"],
                                      db_path, webhook, dry_run=args.dry_run,
                                      per_job=jenkins_cfg["per_job"],
                                      max_log_bytes=jenkins_cfg["max_log_bytes"])
            except Exception as exc:  # une source ne doit pas bloquer l'autre
                print(f"[agent] source {source} : {exc}", file=sys.stderr)

    if args.interval > 0:
        print(f"[agent] boucle : un passage toutes les {args.interval}s (Ctrl-C pour arreter).",
              file=sys.stderr)
        try:
            while True:
                _pass()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0

    _pass()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
