"""Observabilite LLM avec Langfuse (optionnelle, auto-hebergee).

Principe : si le paquet `langfuse` n'est pas installe OU si les cles ne sont pas
configurees, tout fonctionne quand meme (no-op). Le coeur reste stdlib (ADR-0001),
Langfuse est un extra (`pip install -r requirements-agent.txt`, ADR-0005).

Ce que l'on trace (une trace par diagnostic) :
- un span par noeud du graphe (sanitize, classify, diagnose, focus_context, ...)
- une *generation* pour chaque appel Ollama : modele, prompt versionne, tokens
- des *scores* : confiance du modele, preuves ancrees dans le log (anti-hallucination)

Defense en profondeur : le sanitizer est aussi branche comme `mask` du client
Langfuse, donc meme une donnee non nettoyee par erreur est masquee avant export.
"""
import os
from contextlib import contextmanager

from .llm import SYSTEM_PROMPT
from .sanitizer import redact

PROMPT_NAME = "pipeline-doctor-diagnose"

try:  # extra optionnel
    from langfuse import Langfuse, propagate_attributes
except ImportError:  # pragma: no cover - teste via LANGFUSE_DISABLED
    Langfuse = None
    propagate_attributes = None

_client = None


def mask(*, data, **_):
    """Masque les secrets dans tout ce qui part vers Langfuse (texte ou structures)."""
    if isinstance(data, str):
        return redact(data)[0]
    if isinstance(data, dict):
        return {k: mask(data=v) for k, v in data.items()}
    if isinstance(data, list):
        return [mask(data=v) for v in data]
    return data


def enabled() -> bool:
    return (
        Langfuse is not None
        and os.environ.get("LANGFUSE_DISABLED", "").lower() not in ("1", "true")
        and bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))
        and bool(os.environ.get("LANGFUSE_SECRET_KEY"))
    )


def client(**kwargs):
    """Client Langfuse singleton (ou None si desactive). kwargs : tests (span_exporter)."""
    global _client
    if _client is None and (enabled() or kwargs):
        _client = Langfuse(mask=mask, environment=os.environ.get("PD_ENV", "local"), **kwargs)
    return _client


def reset():
    global _client
    _client = None


def system_prompt():
    """Prompt versionne dans Langfuse (label `production`), repli sur le prompt du code.

    Retourne (texte, prompt_client_ou_None). Le prompt_client relie chaque generation
    a la version du prompt : on peut comparer la qualite entre deux versions.
    """
    lf = client()
    if lf is None:
        return SYSTEM_PROMPT, None
    try:
        p = lf.get_prompt(PROMPT_NAME, label="production", fallback=SYSTEM_PROMPT,
                          fetch_timeout_seconds=3, max_retries=0)
        return p.compile(), p
    except Exception:
        return SYSTEM_PROMPT, None


@contextmanager
def trace(name: str, *, session_id=None, tags=None, metadata=None, input=None):
    """Span racine d'un diagnostic. Cede l'objet span (ou None si Langfuse coupe)."""
    lf = client()
    if lf is None:
        yield None
        return
    with lf.start_as_current_observation(name=name, as_type="agent", input=input) as root:
        with propagate_attributes(session_id=session_id, tags=tags or [],
                                  metadata={k: str(v) for k, v in (metadata or {}).items()},
                                  trace_name=name):
            yield root


@contextmanager
def span(name: str, as_type: str = "span", **kwargs):
    lf = client()
    if lf is None:
        yield None
        return
    with lf.start_as_current_observation(name=name, as_type=as_type, **kwargs) as s:
        yield s


def score(name: str, value, comment=None, data_type=None):
    lf = client()
    if lf is not None:
        lf.score_current_trace(name=name, value=value, comment=comment, data_type=data_type)


def current_trace_url():
    lf = client()
    if lf is None:
        return None
    try:
        return lf.get_trace_url()
    except Exception:
        return None


def flush():
    if _client is not None:
        _client.flush()
