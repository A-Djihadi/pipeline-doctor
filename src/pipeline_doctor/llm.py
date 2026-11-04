"""Client Ollama minimal (stdlib uniquement, aucune dependance a installer)."""
import json
import os
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:3b")

SYSTEM_PROMPT = """Tu es un ingenieur DevOps senior. On te donne le log d'un build CI en echec.
Reponds UNIQUEMENT avec un objet JSON de cette forme :
{
  "category": "une valeur parmi : compilation, tests, dependency, network, oom, registry_auth, config, disk, unknown",
  "cause": "cause racine probable en une phrase",
  "evidence": ["lignes copiees mot pour mot depuis le log"],
  "fix": "correctif concret propose, court",
  "confidence": 0.0
}
category decrit la cause racine, pas le symptome : un test qui echoue faute de variable
d'environnement releve de "config", un test qui echoue sur un timeout reseau releve de "network".
Le correctif doit supprimer la cause. Ne propose jamais de contourner l'erreur : pas de --force,
--legacy-peer-deps, --no-verify, tests ignores ou desactives, verification TLS coupee.
confidence est un nombre entre 0 et 1. Si le log ne suffit pas pour conclure, mets une confiance basse
et dis ce qui manque dans "cause". N'invente jamais de lignes qui ne sont pas dans le log."""


def ask_ollama(log: str, timeout: int = 180) -> dict:
    """Diagnostic brut (compatibilite sessions 1-3) : retourne seulement le JSON."""
    return chat(SYSTEM_PROMPT, log, timeout=timeout)[0]


def chat(system_prompt: str, log: str, timeout: int = 180, model: str | None = None) -> tuple[dict, dict]:
    """Appel Ollama /api/chat. Retourne (diagnostic JSON, meta).

    meta contient le modele, les parametres et l'usage en tokens rapportes par Ollama
    (prompt_eval_count / eval_count) : c'est ce que le graphe envoie a Langfuse
    pour suivre cout (ici en tokens, le modele est local) et latence.
    """
    model = model or OLLAMA_MODEL
    options = {"temperature": 0.1}
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": options,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Log du build en echec :\n\n{log}"},
        ],
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    meta = {
        "model": model,
        "model_parameters": options,
        "usage": {
            "input": int(body.get("prompt_eval_count") or 0),
            "output": int(body.get("eval_count") or 0),
        },
    }
    return json.loads(body["message"]["content"]), meta
