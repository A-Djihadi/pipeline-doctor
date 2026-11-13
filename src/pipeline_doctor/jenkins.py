"""Acces lecture seule a l'API Jenkins (stdlib uniquement).

On ne fait que LIRE : lister les builds d'un job, telecharger le log console.
Aucune dependance tierce, aucune action sur l'instance Jenkins.

Les URL sont toujours construites a partir de PD_JENKINS_URL + chemin de job +
numero de build (jamais suivies depuis une reponse). Les identifiants passent
uniquement par l'en-tete `Authorization: Basic` (PD_JENKINS_USER/PD_JENKINS_TOKEN).
"""
import base64
import json
import re
import urllib.request
from urllib.error import HTTPError
from urllib.parse import quote, urlparse

from .github import _SafeRedirect


class JenkinsError(Exception):
    """Erreur Jenkins (identifiants refuses, base invalide, HTTP en echec)."""


_OPENER = urllib.request.build_opener(_SafeRedirect())


def check_base_url(base: str) -> str:
    """Valide la base URL : https obligatoire (http autorise seulement en boucle locale),
    pas d'userinfo (les identifiants passent par PD_JENKINS_USER/PD_JENKINS_TOKEN).
    Retourne la base sans `/` final."""
    base = base.rstrip("/")
    parsed = urlparse(base)
    if "@" in parsed.netloc:
        raise JenkinsError(
            "PD_JENKINS_URL must not contain userinfo "
            "(use PD_JENKINS_USER and PD_JENKINS_TOKEN)")
    if parsed.scheme != "https":
        if not (parsed.scheme == "http"
                and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
            raise JenkinsError("PD_JENKINS_URL must use https")
    return base


def job_url(base: str, job_path: str) -> str:
    """`team/app/main` -> `{base}/job/team/job/app/job/main` ; chaque segment est quote."""
    base = base.rstrip("/")
    quoted = "/job/".join(quote(seg, safe="%") for seg in job_path.split("/"))
    return f"{base}/job/{quoted}"


def build_url(base: str, job_path: str, number: int) -> str:
    return f"{job_url(base, job_path)}/{number}/"


def _http_error(exc: HTTPError) -> JenkinsError:
    if exc.code in (401, 403):
        return JenkinsError(
            f"Jenkins refused credentials for PD_JENKINS_USER (HTTP {exc.code})")
    path = urlparse(exc.url).path or exc.url
    return JenkinsError(f"HTTP {exc.code} on {path}")


def _request(url: str, user: str, token: str, accept: str, timeout: int = 60):
    """Une requete GET (sans corps) avec Basic auth. Retourne la reponse ouverte."""
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": accept,
            "User-Agent": "pipeline-doctor",
        },
    )
    try:
        return _OPENER.open(req, timeout=timeout)
    except HTTPError as exc:
        raise _http_error(exc) from exc


def list_failed_builds(base, job_path, user, token, per_job=10) -> list[int]:
    """Numeros des builds termines en FAILURE, du plus recent au plus ancien."""
    url = f"{job_url(base, job_path)}/api/json?tree=builds[number,result,building]"
    with _request(url, user, token, "application/json") as resp:
        data = json.loads(resp.read())
    builds = sorted(data.get("builds", []), key=lambda b: b.get("number", 0), reverse=True)
    out = []
    for b in builds[:per_job]:
        if b.get("building") is False and b.get("result") == "FAILURE":
            out.append(int(b["number"]))
    return out


def console_log(base, job_path, number, user, token,
                max_tail_bytes=2_097_152, head_bytes=65_536) -> str:
    """Log console `consoleText`, lu par chunks : garde la tete + une queue bornee."""
    url = f"{build_url(base, job_path, number)}consoleText"
    head = bytearray()
    tail = bytearray()
    dropped = 0
    with _request(url, user, token, "text/plain") as resp:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            if len(head) < head_bytes:
                take = head_bytes - len(head)
                head.extend(chunk[:take])
                chunk = chunk[take:]
            if chunk:
                tail.extend(chunk)
                if len(tail) > max_tail_bytes:
                    dropped += len(tail) - max_tail_bytes
                    del tail[: len(tail) - max_tail_bytes]
    body = bytes(head) + bytes(tail)
    if dropped:
        body = bytes(head) + f"\n[... {dropped} bytes omitted ...]\n".encode() + bytes(tail)
    return strip_console_notes(body.decode("utf-8", errors="replace"))


_CONSOLE_NOTE = re.compile(r"\x1b\[8mha:[^\x1b]*\x1b\[0m")


def strip_console_notes(text: str) -> str:
    """Retire les annotations ConsoleNote de Jenkins (payload cache en base64)."""
    return _CONSOLE_NOTE.sub("", text)
