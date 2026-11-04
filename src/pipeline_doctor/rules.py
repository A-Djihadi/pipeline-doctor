"""Classification par regles (stdlib) : categorie d'echec + diagnostic de secours.

Deux usages dans le graphe LangGraph :
1. `classify` : etiqueter le log (maven, npm, tests, docker, oom, reseau...) pour
   orienter le prompt et alimenter les metriques (combien d'echecs par categorie).
2. `fallback_rules` : si Ollama est injoignable, produire quand meme un diagnostic
   deterministe, marque `source = "rules"` (degradation gracieuse, pas de silence).

Chaque regle ne cite comme preuve QUE des lignes reellement presentes dans le log.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    category: str
    pattern: re.Pattern
    cause: str
    fix: str
    confidence: float


# Ordre = priorite : la premiere regle qui matche gagne (le reseau passe avant les
# dependances, car un timeout pypi finit souvent par « No matching distribution »).
RULES = [
    Rule("compilation", re.compile(r"COMPILATION ERROR|cannot find symbol|error: cannot find symbol|"
                                   r"error TS\d{4}:"),
         "Erreur de compilation : un symbole (methode, classe) reference n'existe pas.",
         "Verifier la signature appelee (renommage, methode manquante dans l'interface) puis recompiler.",
         0.7),
    Rule("network", re.compile(r"Could not resolve host|Temporary failure in name resolution|"
                               r"Connection timed out|ETIMEDOUT|ECONNREFUSED|i/o timeout|"
                               r"TLS handshake timeout|\btimed out\b"),
         "Probleme reseau : DNS, timeout ou service distant injoignable.",
         "Rejouer le job ; si persistant, verifier DNS/proxy/pare-feu et l'etat du service distant.",
         0.55),
    Rule("dependency", re.compile(r"npm ERR! code ERESOLVE|Could not resolve dependencies|"
                                  r"ERESOLVE unable to resolve dependency tree|No matching distribution found"),
         "Conflit ou absence de dependance lors de la resolution du gestionnaire de paquets.",
         "Aligner les versions en conflit (lockfile) ou epingler la version compatible.",
         0.65),
    Rule("tests", re.compile(r"Tests run: \d+, Failures: [1-9]|FAILED tests/|AssertionError|"
                             r"\d+ failed, \d+ passed|There (?:was|were) \d+ failures?"),
         "Des tests unitaires echouent (assertion non verifiee).",
         "Lire le premier test en echec, reproduire en local, corriger le code ou le test.",
         0.6),
    Rule("oom", re.compile(r"OOMKilled|exit code 137|java\.lang\.OutOfMemoryError|"
                           r"JavaScript heap out of memory|Killed\s+signal 9"),
         "Le processus a ete tue par manque de memoire (OOM).",
         "Augmenter la memoire du runner/pod ou limiter la heap (-Xmx, --max-old-space-size).",
         0.75),
    Rule("registry_auth", re.compile(r"denied: requested access to the resource is denied|"
                                     r"unauthorized: authentication required|pull access denied|"
                                     r"401 Unauthorized"),
         "Echec d'authentification aupres d'un registre (images ou paquets).",
         "Verifier le secret/identite du pipeline (expiration, droits pull/push sur le registre).",
         0.7),
    Rule("config", re.compile(r"environment variable .* (?:is )?not set|KeyError: '[A-Z_]+'|"
                              r"secret .* not found|Missing required (?:input|variable)"),
         "Configuration manquante : variable d'environnement ou secret absent.",
         "Declarer la variable/secret dans le pipeline (et l'environnement cible).",
         0.6),
]


def _evidence(log: str, pattern: re.Pattern, limit: int = 3) -> list[str]:
    lines = [ln.strip() for ln in log.splitlines() if pattern.search(ln)]
    seen, out = set(), []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            out.append(ln)
        if len(out) == limit:
            break
    return out


def classify(log: str) -> dict:
    """Retourne {category, cause, fix, confidence, evidence} ; category='unknown' si rien."""
    for rule in RULES:
        ev = _evidence(log, rule.pattern)
        if ev:
            return {"category": rule.category, "cause": rule.cause, "fix": rule.fix,
                    "confidence": rule.confidence, "evidence": ev}
    return {"category": "unknown", "cause": "Aucune signature connue dans le log.",
            "fix": "Analyse manuelle necessaire.", "confidence": 0.1, "evidence": []}


ERROR_LINE = re.compile(r"(?i)\b(error|failed|failure|exception|fatal|denied|killed|timeout)\b")


def error_windows(log: str, radius: int = 3, max_chars: int = 6000) -> str:
    """Extrait les fenetres de lignes autour des erreurs (contexte cible pour un 2e essai)."""
    lines = log.splitlines()
    keep = set()
    for i, ln in enumerate(lines):
        if ERROR_LINE.search(ln):
            keep.update(range(max(0, i - radius), min(len(lines), i + radius + 1)))
    if not keep:
        return log[-max_chars:]
    out, prev = [], -2
    for i in sorted(keep):
        if i != prev + 1:
            out.append("[...]")
        out.append(lines[i])
        prev = i
    return "\n".join(out)[-max_chars:]
