"""Orchestration du diagnostic en graphe d'etats LangGraph (extra optionnel).

    sanitize -> classify -> diagnose --(diagnostic fiable)------------> finalize
                               |   \\--(doute, 1er essai)------------> focus_context -> diagnose
                               \\--(Ollama injoignable)------------> fallback_rules -> finalize
                                                      finalize --(--review)--> human_review

Pourquoi un graphe plutot que la fonction lineaire de la session 3 :
- chaque etape est un noeud testable isolement, avec un etat type (`DiagState`) ;
- les decisions (re-essai, repli, validation humaine) sont des aretes conditionnelles
  explicites, visibles sur le schema (`--mermaid`) et dans les traces Langfuse ;
- `human_review` utilise `interrupt()` + un checkpointer : le graphe se met en pause,
  attend la decision humaine, puis reprend (pre-requis de la v2 « PR avec validation »).

Les principes ne changent pas : lecture seule, secrets masques AVANT le modele, local only.
"""
import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from . import tracing
from .llm import chat
from .rules import classify as rules_classify
from .rules import error_windows
from .sanitizer import sanitize as sanitize_log

CONFIDENCE_THRESHOLD = 0.6
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class DiagContext:
    """Donnees d'execution hors etat : ni tracees en entree des noeuds, ni checkpointees."""
    raw_log: str             # log brut, lu uniquement par `sanitize`


class DiagState(TypedDict, total=False):
    log: str                 # log nettoye (le log brut reste dans DiagContext)
    n_redacted: int
    category: str            # etiquette par regles (maven, oom, reseau...)
    rule_hint: dict          # diagnostic par regles (repli)
    context: str             # ce qui est envoye au modele (log complet ou fenetres d'erreur)
    attempts: int
    diagnosis: dict          # {category, cause, evidence, fix, confidence}
    source: str              # "llm" | "rules"
    llm_error: str
    grounded: float          # part des preuves reellement presentes dans le log
    retry_reasons: list      # doutes qui ont declenche le re-essai (confidence, grounding, category)
    review: str              # "approved" | "rejected" (si --review)


def grounding(diagnosis: dict, log: str) -> float:
    """Part des preuves citees presentes mot pour mot dans le log (0 si aucune preuve)."""
    evidence = [e for e in diagnosis.get("evidence", []) if isinstance(e, str) and e.strip()]
    return round(sum(1 for e in evidence if e.strip() in log) / len(evidence), 2) if evidence else 0.0


def doubts(state: DiagState) -> list[str]:
    """Raisons de douter d'un diagnostic LLM, de la plus faible a la plus verifiable.

    La confiance est auto-declaree (un petit modele repond presque toujours 0.9) : on la
    complete par deux signaux verifiables, l'ancrage des preuves dans le log et l'accord
    entre la categorie du modele et celle des regles (quand les regles en ont trouve une).
    """
    d = state.get("diagnosis") or {}
    reasons = []
    if float(d.get("confidence") or 0.0) < CONFIDENCE_THRESHOLD:
        reasons.append("confidence")
    if grounding(d, state["log"]) < 1.0:
        reasons.append("grounding")
    rules_category, llm_category = state.get("category", "unknown"), d.get("category")
    if rules_category != "unknown" and llm_category and llm_category != rules_category:
        reasons.append("category")
    return reasons


# ---------------------------------------------------------------- noeuds

def sanitize(state: DiagState, runtime: Runtime[DiagContext]) -> DiagState:
    cleaned, n = sanitize_log(runtime.context.raw_log)
    return {"log": cleaned, "context": cleaned, "n_redacted": n, "attempts": 0}


def classify(state: DiagState) -> DiagState:
    hint = rules_classify(state["log"])
    return {"category": hint["category"], "rule_hint": hint}


def diagnose(state: DiagState) -> DiagState:
    """Appel du modele local. Une *generation* Langfuse par appel (prompt, tokens, latence)."""
    prompt_text, prompt_client = tracing.system_prompt()
    system = (f"{prompt_text}\nIndice (classification par regles) : categorie probable = "
              f"{state.get('category', 'unknown')}.")
    attempt = state.get("attempts", 0) + 1
    with tracing.span("ollama-chat", as_type="generation", input=state["context"],
                      prompt=prompt_client, metadata={"attempt": attempt}) as gen:
        try:
            diagnosis, meta = chat(system, state["context"])
        except Exception as exc:  # Ollama injoignable, timeout, JSON invalide
            if gen is not None:
                gen.update(level="ERROR", status_message=str(exc))
            return {"attempts": attempt, "llm_error": str(exc)}
        if gen is not None:
            gen.update(output=diagnosis, model=meta["model"],
                       model_parameters=meta["model_parameters"], usage_details=meta["usage"])
    return {"attempts": attempt, "diagnosis": diagnosis, "source": "llm", "llm_error": ""}


def focus_context(state: DiagState) -> DiagState:
    """Diagnostic douteux : on re-essaie avec un contexte resserre autour des lignes d'erreur."""
    return {"context": error_windows(state["log"]), "retry_reasons": doubts(state)}


def fallback_rules(state: DiagState) -> DiagState:
    hint = dict(state["rule_hint"])
    hint.pop("category", None)
    return {"diagnosis": hint, "source": "rules"}


def finalize(state: DiagState) -> DiagState:
    """Mesure d'ancrage : une preuve citee doit exister dans le log (sinon hallucination)."""
    d = state.get("diagnosis") or {}
    grounded = grounding(d, state["log"])
    tracing.score("confidence", float(d.get("confidence") or 0.0))
    tracing.score("evidence_grounded", grounded,
                  comment="part des preuves presentes mot pour mot dans le log")
    tracing.score("source", state.get("source", "none"), data_type="CATEGORICAL")
    if state.get("source") == "llm" and d.get("category") and state.get("category", "unknown") != "unknown":
        tracing.score("category_agreement", float(d["category"] == state["category"]),
                      comment="categorie du modele == categorie des regles")
    return {"grounded": grounded}


def human_review(state: DiagState) -> DiagState:
    """Pause du graphe : un humain valide le correctif avant toute action (ici : aucune)."""
    decision = interrupt({"question": "Valider ce correctif ?", "diagnosis": state.get("diagnosis")})
    return {"review": "approved" if str(decision).lower() in ("y", "yes", "o", "oui") else "rejected"}


# ---------------------------------------------------------------- aretes

def route_after_diagnose(state: DiagState) -> Literal["finalize", "focus_context", "fallback_rules"]:
    if state.get("llm_error"):
        return "fallback_rules"
    if doubts(state) and state.get("attempts", 0) < MAX_ATTEMPTS:
        return "focus_context"
    return "finalize"


def build(review: bool = False):
    g = StateGraph(DiagState, context_schema=DiagContext)
    for name, fn in [("sanitize", sanitize), ("classify", classify), ("diagnose", diagnose),
                     ("focus_context", focus_context), ("fallback_rules", fallback_rules),
                     ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "sanitize")
    g.add_edge("sanitize", "classify")
    g.add_edge("classify", "diagnose")
    g.add_conditional_edges("diagnose", route_after_diagnose)
    g.add_edge("focus_context", "diagnose")
    g.add_edge("fallback_rules", "finalize")
    if review:
        g.add_node("human_review", human_review)
        g.add_edge("finalize", "human_review")
        g.add_edge("human_review", END)
        return g.compile(checkpointer=InMemorySaver())
    g.add_edge("finalize", END)
    return g.compile()


def _callbacks():
    """Handler LangChain de Langfuse : un span par noeud du graphe, automatiquement."""
    if tracing.client() is None:
        return []
    from langfuse.langchain import CallbackHandler
    return [CallbackHandler()]


def run(raw_log: str, *, review: bool = False, ask=input, session_id=None, metadata=None) -> dict:
    """Execute le graphe et retourne l'etat final (+ chemin parcouru, duree, url de trace)."""
    app = build(review=review)
    thread = {"configurable": {"thread_id": str(uuid.uuid4())}, "callbacks": _callbacks()}
    path, final = [], {}
    started = time.monotonic()
    with tracing.trace("pipeline-doctor.diagnose", session_id=session_id,
                       tags=["pipeline-doctor", "langgraph"], metadata=metadata,
                       input={"chars": len(raw_log)}) as root:
        # Le log brut passe par le contexte d'execution, pas par l'etat : le CallbackHandler
        # trace l'etat en entree de chaque noeud, il n'y verra que le log nettoye.
        context = DiagContext(raw_log=raw_log)
        payload = {"attempts": 0}
        while True:
            for update in app.stream(payload, thread, stream_mode="updates", context=context):
                for node, values in update.items():
                    if node == "__interrupt__":
                        continue
                    path.append(node)
                    final.update(values or {})
            if not review:
                break
            snapshot = app.get_state(thread)
            if not snapshot.next:
                break
            question = snapshot.tasks[0].interrupts[0].value
            d = question.get("diagnosis") or {}
            answer = ask(f"\n[revue] cause    : {d.get('cause')}\n[revue] correctif: {d.get('fix')}\n"
                         f"{question['question']} [o/N] ")
            payload = Command(resume=answer)
        url = tracing.current_trace_url()
        if root is not None:
            root.update(output={k: final.get(k) for k in ("category", "source", "diagnosis", "grounded")})
    tracing.flush()
    final.update({"path": path, "duration_s": round(time.monotonic() - started, 2), "trace_url": url})
    return final


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline-doctor-graph",
                                     description="Diagnostic CI orchestre par LangGraph (trace Langfuse).")
    parser.add_argument("logfile", nargs="?", help="chemin du log, ou - pour stdin")
    parser.add_argument("--review", action="store_true", help="validation humaine (interrupt) en fin de graphe")
    parser.add_argument("--mermaid", action="store_true", help="affiche le schema du graphe (Mermaid) et sort")
    parser.add_argument("--json", action="store_true", help="sortie JSON brute")
    args = parser.parse_args(argv)

    if args.mermaid:
        print(build(review=True).get_graph().draw_mermaid())
        return 0
    if not args.logfile:
        parser.error("logfile requis")
    raw = sys.stdin.read() if args.logfile == "-" else open(args.logfile, encoding="utf-8", errors="replace").read()
    result = run(raw, review=args.review, metadata={"file": args.logfile})

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    d = result.get("diagnosis") or {}
    print(f"[graph] chemin    : {' -> '.join(result['path'])}", file=sys.stderr)
    print(f"[graph] secrets   : {result.get('n_redacted', 0)} masque(s) avant le modele", file=sys.stderr)
    if result.get("retry_reasons"):
        print(f"[graph] re-essai  : {', '.join(result['retry_reasons'])}", file=sys.stderr)
    if result.get("llm_error"):
        print(f"[graph] ollama    : indisponible ({result['llm_error'][:80]}) -> repli regles", file=sys.stderr)
    print(f"\nCategorie  : {result.get('category')}")
    print(f"Source     : {result.get('source')}  (essais LLM : {result.get('attempts', 0)})")
    print(f"Cause      : {d.get('cause')}")
    print(f"Confiance  : {d.get('confidence')}   Preuves ancrees : {result.get('grounded')}")
    for line in d.get("evidence", []):
        print(f"  > {line}")
    print(f"Correctif  : {d.get('fix')}")
    if result.get("review"):
        print(f"Revue      : {result['review']}")
    if result.get("trace_url"):
        print(f"Trace      : {result['trace_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
