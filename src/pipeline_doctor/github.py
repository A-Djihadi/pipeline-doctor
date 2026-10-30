"""Acces lecture seule a l'API GitHub Actions (stdlib uniquement).

On ne fait que LIRE : lister les jobs d'un run, recuperer le log d'un job.
Aucune dependance tierce, aucune ecriture sur le depot.
"""
import json
import os
import urllib.request

API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")


def _request(url: str, token: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pipeline-doctor",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def list_jobs(repo: str, run_id: str, token: str) -> list[dict]:
    """Liste les jobs d'un run d'Action (id, name, conclusion, status, ...)."""
    url = f"{API_URL}/repos/{repo}/actions/runs/{run_id}/jobs"
    return json.loads(_request(url, token)).get("jobs", [])


def job_log(repo: str, job_id: str, token: str) -> str:
    """Log brut d'un job (texte, pas de zip ni de redirection)."""
    url = f"{API_URL}/repos/{repo}/actions/jobs/{job_id}/logs"
    return _request(url, token).decode("utf-8", errors="replace")


def failed_job_ids(jobs: list[dict]) -> list[int]:
    """IDs des jobs dont la conclusion est 'failure'."""
    return [int(j["id"]) for j in jobs if j.get("conclusion") == "failure"]


def run_url(repo: str, run_id: str) -> str:
    return f"https://github.com/{repo}/actions/runs/{run_id}"
