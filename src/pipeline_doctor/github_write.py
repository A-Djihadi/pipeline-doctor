"""Ecriture minimale vers GitHub (fork du bot + PR draft). SEUL module non-GET.

Le token du bot n'a que le scope `public_repo`. Aucune capacite merge/review/label/
comment/ready n'existe ici, meme derriere un flag. Seul `POST /pulls` cible le
depot upstream ; tout le reste ecrit sur le fork.
"""
import json
import urllib.request
from urllib.error import HTTPError

from .github import API_URL, _SafeRedirect


class GitHubWriteError(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(f"github write error {status}: {reason}")
        self.status = status
        self.reason = reason


_OPENER = urllib.request.build_opener(_SafeRedirect())


def _request(method: str, url: str, token: str, body: dict | None = None):
    """Une requete (GET ou POST, JSON) ; erreurs HTTP sans jamais exposer le token."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pipeline-doctor",
            "Content-Type": "application/json",
        },
    )
    try:
        with _OPENER.open(req, timeout=60) as resp:
            raw = resp.read()
    except HTTPError as exc:
        raise GitHubWriteError(exc.code, f"github_error:{exc.code}") from exc
    if not raw:
        return None
    return json.loads(raw)


def sync_fork(fork: str, branch: str, token: str) -> None:
    """Synchronise la branche du fork avec l'upstream."""
    _request("POST", f"{API_URL}/repos/{fork}/merge-upstream", token, {"branch": branch})


def commit_files(fork: str, parent_sha: str, files: dict[str, str], message: str,
                 token: str) -> str:
    """Cree un commit sur le fork (blobs + tree + commit) ; retourne le SHA."""
    parent = _request("GET", f"{API_URL}/repos/{fork}/git/commits/{parent_sha}", token)
    tree_sha = parent["tree"]["sha"]

    entries = []
    for path, content in files.items():
        blob = _request("POST", f"{API_URL}/repos/{fork}/git/blobs", token,
                        {"content": content, "encoding": "utf-8"})
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

    tree = _request("POST", f"{API_URL}/repos/{fork}/git/trees", token,
                    {"base_tree": tree_sha, "tree": entries})
    commit = _request("POST", f"{API_URL}/repos/{fork}/git/commits", token,
                      {"message": message, "tree": tree["sha"], "parents": [parent_sha]})
    return commit["sha"]


def create_branch(fork: str, branch: str, sha: str, token: str) -> None:
    """Cree une ref de branche sur le fork."""
    try:
        _request("POST", f"{API_URL}/repos/{fork}/git/refs", token,
                 {"ref": f"refs/heads/{branch}", "sha": sha})
    except GitHubWriteError as exc:
        if exc.status == 422:
            raise GitHubWriteError(422, "branch_exists") from exc
        raise


def open_draft_pr(upstream: str, head: str, base: str, title: str, body: str,
                  token: str) -> tuple[int, str]:
    """Ouvre une PR *draft* du fork vers l'upstream. Retourne (number, html_url)."""
    resp = _request("POST", f"{API_URL}/repos/{upstream}/pulls", token,
                    {"title": title, "head": head, "base": base, "body": body,
                     "draft": True, "maintainer_can_modify": True})
    if not resp or resp.get("draft") is not True:
        raise GitHubWriteError(422, "draft_refused")
    return resp["number"], resp["html_url"]
