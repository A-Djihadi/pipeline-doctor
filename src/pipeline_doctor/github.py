"""Acces lecture seule a l'API GitHub Actions (stdlib uniquement).

On ne fait que LIRE : lister les jobs d'un run, recuperer le log d'un job.
Aucune dependance tierce, aucune ecriture sur le depot.
"""
import json
import os
import urllib.request
from urllib.parse import urlparse

API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Suivre une redirection sans repasser `Authorization` vers un autre hote.

    Le endpoint des logs renvoie un 302 vers un blob Azure signe (SAS) : lui
    renvoyer `Authorization: Bearer ...` entraine un 401.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urlparse(new.full_url).netloc != urlparse(req.full_url).netloc:
            new.headers = {k: v for k, v in new.headers.items()
                           if k.lower() != "authorization"}
        return new


_OPENER = urllib.request.build_opener(_SafeRedirect())


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
    with _OPENER.open(req, timeout=timeout) as resp:
        return resp.read()


def list_jobs(repo: str, run_id: str, token: str) -> list[dict]:
    """Liste les jobs d'un run d'Action (id, name, conclusion, status, ...)."""
    url = f"{API_URL}/repos/{repo}/actions/runs/{run_id}/jobs"
    return json.loads(_request(url, token)).get("jobs", [])


def list_runs(repo: str, workflow: str, token: str, per_page: int = 10) -> list[dict]:
    """Derniers runs d'un workflow (workflow = nom de fichier, ex. ci.yml)."""
    url = f"{API_URL}/repos/{repo}/actions/workflows/{workflow}/runs?per_page={per_page}"
    return json.loads(_request(url, token)).get("workflow_runs", [])


def job_log(repo: str, job_id: str, token: str) -> str:
    """Log brut d'un job (texte, pas de zip ni de redirection)."""
    url = f"{API_URL}/repos/{repo}/actions/jobs/{job_id}/logs"
    return _request(url, token).decode("utf-8", errors="replace")


def failed_job_ids(jobs: list[dict]) -> list[int]:
    """IDs des jobs dont la conclusion est 'failure'."""
    return [int(j["id"]) for j in jobs if j.get("conclusion") == "failure"]


def run_url(repo: str, run_id: str) -> str:
    return f"https://github.com/{repo}/actions/runs/{run_id}"
