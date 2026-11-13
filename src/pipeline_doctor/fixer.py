"""Logique pure du correctif (aucune I/O, aucun reseau, aucun acces a l'environnement).

Le modele (Ollama) est un callable injecte `ask(system, user) -> dict`. Toutes les
decisions deterministes (eligibilite, candidats, denylist, application, gardes)
vivent ici, testables avec des dicts, sans dependance.
"""
import difflib
import fnmatch
import re
from dataclasses import dataclass

from . import rules
from .sanitizer import redact

FIXABLE_CATEGORIES = frozenset({"compilation", "dependency", "tests"})
MAX_FILES = 3
MAX_FILE_CHARS = 20_000
MAX_CHANGED_LINES = 40
MIN_CONFIDENCE = 0.6
FIX_PROMPT_VERSION = "fix-v1"
BRANCH_PREFIX = "pipeline-doctor/fix-"


class FixRejected(Exception):
    """Le correctif est refuse (ou aucun correctif possible). `status` in
    {"skipped", "no_fix", "rejected"}."""

    def __init__(self, status: str, reason: str):
        super().__init__(f"{status}:{reason}")
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Edit:
    path: str
    search: str
    replace: str


@dataclass(frozen=True)
class Proposal:
    files: tuple[str, ...]        # chemins edites, tries
    original: dict[str, str]      # path -> texte original (tel que lu)
    patched: dict[str, str]       # path -> texte corrige (style EOL d'origine restaure)
    diff: str                     # diff unifie, "a/<path>" "b/<path>", 3 lignes de contexte
    summary: str                  # resume du modele, <= 200 chars, une ligne
    confidence: float


FIX_SYSTEM_PROMPT = """Tu es un ingenieur DevOps senior. On te donne le diagnostic d'un build CI en echec et
le contenu exact de quelques fichiers du depot. Reponds UNIQUEMENT avec un objet JSON de cette forme :
{"edits": [{"path": "...", "search": "...", "replace": "..."}], "summary": "...", "confidence": 0.0}
- N'edite que les fichiers listes dans « Files you may edit ».
- « search » doit etre copie mot pour mot depuis le fichier et etre unique dans ce fichier.
- Propose le plus petit changement qui supprime la cause racine.
- Renvoie {"edits": []} si aucun changement sur et correct n'existe.
- N'utilise jamais de contournement : pas de --force, --legacy-peer-deps, --no-verify,
  pas de tests ignores ou desactives, pas de verification TLS coupee, pas de chmod 777.
- N'ajoute jamais d'identifiants, de tokens ni d'URL avec identifiants.
- N'edite jamais la configuration CI.
- « summary » est une phrase, « confidence » un nombre entre 0 et 1."""


# ---------------------------------------------------------------- denylist

_DENY_PATTERNS = (
    ".github/*", ".gitlab-ci.yml", ".circleci/*", "jenkinsfile*",
    "azure-pipelines*.yml", ".env*", "*.pem", "*.key", "*.p12", "*.pfx",
    "id_rsa*", "id_ed25519*", "*secret*", "*credential*", "*.tfstate*",
    "*kubeconfig*", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
    "pnpm-lock.yaml", "poetry.lock", "pipfile.lock", "uv.lock", "cargo.lock",
    "go.sum", "gemfile.lock", "composer.lock", "codeowners", ".gitmodules",
    ".gitattributes", ".pre-commit-config.yaml",
)


def is_denied(path: str) -> bool:
    """Chemin interdit (CI, secrets, lockfiles, ...), insensible a la casse."""
    p = path.lower()
    segments = p.split("/")
    if p.startswith("/") or ".." in segments:
        return True
    if ".github" in segments or ".circleci" in segments:
        return True
    basename = segments[-1]
    for pattern in _DENY_PATTERNS:
        if fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(basename, pattern):
            return True
    return False


# ---------------------------------------------------------------- candidats

# Extensions reconnues comme fichiers de code/configuration editables.
_EXT = (r"pyi|py|java|kts|kt|scala|groovy|jsx|mjs|cjs|js|tsx|ts|go|rs|rb|php|"
        r"cs|cc|cpp|c|hpp|h|swift|xml|json|toml|ya?ml|cfg|ini|txt|gradle|properties|sql")
_PATH_TOKEN = re.compile(r"[\w./\\-]+\.(?:" + _EXT + r")")

_MANIFESTS = ("package.json", "requirements.txt", "pyproject.toml", "setup.cfg",
              "pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "Cargo.toml",
              "Gemfile", "composer.json")


def _strip_linecol(token: str) -> str:
    """Retire un suffixe `:<ligne>[:<col>]` ou `:[<l>,<c>]` (deja exclu du regex)."""
    token = re.sub(r":\d+(?::\d+)?$", "", token)
    token = re.sub(r":\[\d+,\d+\]$", "", token)
    return token


def _map_token(token: str, index: dict) -> str | None:
    """Resout un jeton vers un chemin d'arbre par plus long suffixe sur `/`."""
    parts = token.split("/")
    for i in range(len(parts)):
        tail = "/".join(parts[i:])
        if tail in index:
            candidates = index[tail]
            if len(candidates) == 1:
                return candidates[0]
            return None  # suffixe ambigu : deux chemins de meme longueur
    return None


def candidate_paths(log: str, tree: dict[str, str], category: str) -> list[str]:
    """Chemins du `tree` a proposer au modele, dans l'ordre de premiere apparition."""
    index = {}
    for path in tree:
        parts = path.split("/")
        for i in range(len(parts)):
            index.setdefault("/".join(parts[i:]), []).append(path)

    seen: list[str] = []

    def add_from(text: str) -> None:
        for m in _PATH_TOKEN.finditer(text):
            token = _strip_linecol(m.group(0)).replace("\\", "/")
            mapped = _map_token(token, index)
            if mapped and mapped not in seen:
                seen.append(mapped)

    add_from(rules.error_windows(log))
    add_from(log)

    if category == "dependency":
        for manifest in _MANIFESTS:
            if manifest in tree and manifest not in seen:
                seen.append(manifest)

    out: list[str] = []
    for path in seen:
        if is_denied(path):
            continue
        if tree.get(path) != "100644":
            continue
        out.append(path)
        if len(out) == MAX_FILES:
            break
    return out


def select_files(fetched: dict[str, str | None]) -> dict[str, str]:
    """Garde les fichiers UTF-8, bornes en taille et sans secret."""
    kept: dict[str, str] = {}
    secret = False
    for path, text in fetched.items():
        if text is None:
            continue
        if len(text) > MAX_FILE_CHARS:
            continue
        if redact(text)[1] > 0:
            secret = True
            continue
        kept[path] = text
    if not kept:
        if secret:
            raise FixRejected("skipped", "secret_in_source")
        raise FixRejected("skipped", "no_candidate_files")
    return kept


# ---------------------------------------------------------------- prompt

def build_prompt(diagnosis: dict, log: str, files: dict[str, str]) -> str:
    """Message utilisateur : diagnostic + fenetres d'erreur + fichiers editables."""
    lines = [
        "Diagnostic",
        f"category: {diagnosis.get('category', '')}",
        f"cause: {diagnosis.get('cause', '')}",
        f"suggested fix: {diagnosis.get('fix', '')}",
        "",
        "Error lines (sanitized):",
        rules.error_windows(log)[-4000:],
    ]
    for path, text in files.items():
        lines.append(f"=== {path}")
        lines.append(text.replace("\r\n", "\n").rstrip("\n"))
    return "\n".join(lines)


# ---------------------------------------------------------------- parsing

def parse_edits(obj: dict) -> tuple[list[Edit], str, float]:
    """Valide la reponse du modele. Leve FixRejected("no_fix", ...) sinon."""
    if not isinstance(obj, dict):
        raise FixRejected("no_fix", "bad_json")
    raw = obj.get("edits")
    if not isinstance(raw, list):
        raise FixRejected("no_fix", "bad_json")
    if not raw:
        raise FixRejected("no_fix", "no_edits")
    edits: list[Edit] = []
    for item in raw:
        if not isinstance(item, dict):
            raise FixRejected("no_fix", "bad_json")
        path, search, replace = item.get("path"), item.get("search"), item.get("replace")
        if not (isinstance(path, str) and isinstance(search, str) and search
                and isinstance(replace, str)):
            raise FixRejected("no_fix", "bad_json")
        edits.append(Edit(path=path, search=search, replace=replace))
    summary = " ".join(str(obj.get("summary") or "").splitlines())[:200]
    try:
        confidence = float(obj.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return edits, summary, confidence


# ---------------------------------------------------------------- application

def apply_edits(files: dict[str, str], edits: list[Edit]) -> dict[str, str]:
    """Applique les replacements exacts, en preservant EOL et saut de ligne final."""
    lf = {p: t.replace("\r\n", "\n") for p, t in files.items()}
    crlf = {p: t.count("\r\n") > (t.count("\n") - t.count("\r\n")) for p, t in files.items()}
    nl = {p: t.endswith("\n") for p, t in files.items()}

    for edit in edits:
        if edit.path not in lf:
            raise FixRejected("rejected", "unknown_path")
        current = lf[edit.path]
        n = current.count(edit.search)
        if n == 0:
            raise FixRejected("rejected", "search_not_found")
        if n > 1:
            raise FixRejected("rejected", "search_not_unique")
        lf[edit.path] = current.replace(edit.search, edit.replace, 1)

    out: dict[str, str] = {}
    for p, text in lf.items():
        if nl[p]:
            if not text.endswith("\n"):
                text += "\n"
        elif text.endswith("\n"):
            text = text.rstrip("\n")
        if crlf[p]:
            text = text.replace("\n", "\r\n")
        out[p] = text

    if all(out[p] == files[p] for p in files):
        raise FixRejected("rejected", "no_change")
    return out


def render_diff(original: dict[str, str], patched: dict[str, str]) -> str:
    """Diff unifie, fichiers tries, seuls les fichiers changes."""
    parts: list[str] = []
    for p in sorted(original):
        if p not in patched or original[p] == patched[p]:
            continue
        a = original[p].replace("\r\n", "\n").splitlines(keepends=True)
        b = patched[p].replace("\r\n", "\n").splitlines(keepends=True)
        parts.extend(difflib.unified_diff(a, b, f"a/{p}", f"b/{p}", n=3))
    return "".join(parts)


# ---------------------------------------------------------------- gardes

def guard(original: dict[str, str], patched: dict[str, str], diff: str) -> None:
    """Toutes les gardes, dans l'ordre ; la premiere violation leve FixRejected."""
    changed = [p for p in patched if patched.get(p) != original.get(p)]
    if len(changed) > MAX_FILES:
        raise FixRejected("rejected", "too_many_files")

    added = [ln[1:] for ln in diff.splitlines()
             if ln.startswith("+") and not ln.startswith("+++")]
    removed = [ln[1:] for ln in diff.splitlines()
               if ln.startswith("-") and not ln.startswith("---")]
    if len(added) + len(removed) > MAX_CHANGED_LINES:
        raise FixRejected("rejected", "too_many_lines")

    for p in changed:
        if is_denied(p):
            raise FixRejected("rejected", "denied_path")
        text = patched[p]
        if redact(text)[1] > 0:
            raise FixRejected("rejected", "secret_in_patch")
        if "[REDACTED" in text:
            raise FixRejected("rejected", "redacted_placeholder")
    if redact(diff)[1] > 0:
        raise FixRejected("rejected", "secret_in_patch")

    for line in added:
        if rules.UNSAFE_FIX.search(line):
            raise FixRejected("rejected", "unsafe_fix")


# ---------------------------------------------------------------- eligibilite

def eligibility(diagnosis: dict | None, log: str, *, source: str) -> str | None:
    """Premiere regle qui echoue gagne ; None si le run est eligible."""
    if diagnosis is None or source != "llm":
        return "not_llm"
    category = diagnosis.get("category")
    if category not in FIXABLE_CATEGORIES:
        return f"category:{category}"
    try:
        confidence = float(diagnosis.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < MIN_CONFIDENCE:
        return "low_confidence"
    if rules.grounding(diagnosis, log) != 1.0:
        return "ungrounded"
    rules_category = rules.classify(log)["category"]
    if rules_category not in ("unknown", category):
        return "category_disagreement"
    return None


# ---------------------------------------------------------------- pipeline

def propose(diagnosis: dict, log: str, files: dict[str, str], ask) -> Proposal:
    """build_prompt -> ask -> parse -> apply -> diff -> guard -> Proposal."""
    try:
        user = build_prompt(diagnosis, log, files)
        obj = ask(FIX_SYSTEM_PROMPT, user)
    except Exception:
        raise FixRejected("no_fix", "llm_error") from None
    edits, summary, confidence = parse_edits(obj)
    patched = apply_edits(files, edits)
    diff = render_diff(files, patched)
    guard(files, patched, diff)
    changed = sorted(p for p in patched if patched[p] != files[p])
    return Proposal(
        files=tuple(changed),
        original={p: files[p] for p in changed},
        patched={p: patched[p] for p in changed},
        diff=diff,
        summary=summary,
        confidence=confidence,
    )
