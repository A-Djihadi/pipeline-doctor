"""Nettoyage des logs avant envoi a un modele (surtout cloud).

Deux objectifs : masquer les secrets, et tronquer les logs trop longs
en gardant les parties utiles (debut + fin, ou la fin est la ou ca casse).
"""
import re

# (nom, regex) -> remplace par [REDACTED:nom]
SECRET_PATTERNS = [
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("url_credentials", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")),
    # cle=valeur avec un nom evocateur : password=..., api_key: ..., secret=...
    ("kv_secret", re.compile(
        r"(?i)\b([A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key)[A-Za-z0-9_.-]*)"
        r"(\s*[=:]\s*)(\"[^\"]+\"|'[^']+'|\S+)")),
]

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def redact(text: str) -> tuple[str, int]:
    """Retourne (texte nettoye, nombre de remplacements)."""
    count = 0
    for name, pattern in SECRET_PATTERNS:
        if name == "kv_secret":
            text, n = pattern.subn(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:secret]", text)
        else:
            text, n = pattern.subn(f"[REDACTED:{name}]", text)
        count += n
    return text, count


def truncate(text: str, max_chars: int = 12000, head: int = 2000) -> str:
    """Garde le debut et surtout la fin du log (la ou l'erreur se trouve)."""
    if len(text) <= max_chars:
        return text
    tail = max_chars - head
    return f"{text[:head]}\n\n[... {len(text) - max_chars} caracteres omis ...]\n\n{text[-tail:]}"


def sanitize(raw: str, max_chars: int = 12000) -> tuple[str, int]:
    cleaned = ANSI.sub("", raw)
    cleaned, n = redact(cleaned)
    return truncate(cleaned, max_chars), n
