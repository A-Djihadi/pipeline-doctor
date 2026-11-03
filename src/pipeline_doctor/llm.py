"""Client Ollama minimal (stdlib uniquement, aucune dependance a installer)."""
import json
import os
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:3b")

DEEPSEEK_URL = os.environ.get("PD_DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.environ.get("PD_DEEPSEEK_MODEL", "deepseek-chat")

SYSTEM_PROMPT = """Tu es un ingenieur DevOps senior. On te donne le log d'un build CI en echec.
Reponds UNIQUEMENT avec un objet JSON de cette forme :
{
  "cause": "cause probable en une phrase",
  "evidence": ["lignes exactes du log qui le prouvent"],
  "fix": "correctif concret propose, court",
  "confidence": 0.0
}
confidence est un nombre entre 0 et 1. Si le log ne suffit pas pour conclure, mets une confiance basse
et dis ce qui manque dans "cause". N'invente jamais de lignes qui ne sont pas dans le log."""


def ask_ollama(log: str, timeout: int = 180) -> dict:
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
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
    return json.loads(body["message"]["content"])


def ask_deepseek(log: str, timeout: int = 180) -> dict:
    api_key = os.environ.get("PD_DEEPSEEK_API_KEY", "")
    if not api_key:
        raise ValueError("PD_DEEPSEEK_API_KEY absent")
    payload = {
        "model": DEEPSEEK_MODEL,
        "stream": False,
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Log du build en echec :\n\n{log}"},
        ],
    }
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    return json.loads(body["choices"][0]["message"]["content"])
