"""Evaluation du diagnostic sur un jeu de logs connus (examples/ + expected.json).

    python -m pipeline_doctor.evals                 # rapport local (tableau)
    python -m pipeline_doctor.evals --langfuse      # + experience Langfuse (dataset, run, scores)

Evaluateurs (deterministes, pas de LLM-juge : reproductibles et gratuits) :
- category_match    : la classification trouve la bonne famille d'echec
- cause_keyword     : la cause/le correctif contient au moins un mot-cle attendu
- evidence_grounded : part des preuves citees presentes mot pour mot dans le log
                      (mesure directe de l'hallucination)
- no_secret_leak    : aucun secret du log brut ne figure dans la sortie

C'est l'equivalent d'une suite de tests de non-regression, mais pour la qualite
d'un composant probabiliste : on la rejoue a chaque changement de prompt ou de modele.
"""
import argparse
import json
import os
import re
import sys

from . import graph, tracing
from .sanitizer import SECRET_PATTERNS

DATASET = "pipeline-doctor-ci-logs"


def load(examples_dir: str) -> list[dict]:
    expected = json.load(open(os.path.join(examples_dir, "expected.json"), encoding="utf-8"))
    items = []
    for name, exp in expected.items():
        if name.startswith("_"):
            continue
        log = open(os.path.join(examples_dir, name), encoding="utf-8").read()
        items.append({"input": {"file": name, "log": log}, "expected_output": exp})
    return items


def _secrets(raw: str) -> list[str]:
    found = []
    for name, pattern in SECRET_PATTERNS:
        for m in pattern.finditer(raw):
            found.append(m.group(3) if name == "kv_secret" else m.group(0))
    return [s for s in found if len(s) >= 6]


def evaluate(raw_log: str, expected: dict, result: dict) -> dict:
    d = result.get("diagnosis") or {}
    text = f"{d.get('cause', '')} {d.get('fix', '')}".lower()
    out = json.dumps(result, ensure_ascii=False)
    return {
        "category_match": float(result.get("category") == expected["category"]),
        "cause_keyword": float(any(k.lower() in text for k in expected["keywords"])),
        "evidence_grounded": float(result.get("grounded") or 0.0),
        "no_secret_leak": float(not any(s in out for s in _secrets(raw_log))),
    }


def run_local(items: list[dict]) -> list[dict]:
    rows = []
    for it in items:
        res = graph.run(it["input"]["log"], metadata={"file": it["input"]["file"], "eval": "local"})
        scores = evaluate(it["input"]["log"], it["expected_output"], res)
        rows.append({"file": it["input"]["file"], "source": res.get("source"),
                     "path": "->".join(res["path"]), "duration_s": res["duration_s"], **scores})
    return rows


def run_langfuse(items: list[dict], run_name: str):
    """Pousse le dataset dans Langfuse puis lance une experience (une trace par item)."""
    lf = tracing.client()
    if lf is None:
        raise SystemExit("Langfuse non configure (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL).")
    from langfuse import Evaluation

    lf.create_dataset(name=DATASET, description="Logs CI en echec + categorie et mots-cles attendus")
    for it in items:  # id stable = idempotent (re-run sans doublons)
        lf.create_dataset_item(dataset_name=DATASET, id=re.sub(r"\W", "-", it["input"]["file"]),
                               input=it["input"], expected_output=it["expected_output"])
    dataset = lf.get_dataset(DATASET)

    def task(*, item, **_):
        inp = item.input if hasattr(item, "input") else item["input"]
        return graph.run(inp["log"], metadata={"file": inp["file"], "eval": run_name})

    def make(name):
        def ev(*, input, output, expected_output, **_):
            return Evaluation(name=name, value=evaluate(input["log"], expected_output, output)[name])
        return ev

    result = lf.run_experiment(name="pipeline-doctor-eval", run_name=run_name, data=dataset.items,
                               task=task, max_concurrency=1,
                               evaluators=[make(n) for n in ("category_match", "cause_keyword",
                                                             "evidence_grounded", "no_secret_leak")])
    lf.flush()
    return result


def print_table(rows: list[dict]) -> None:
    cols = ["file", "source", "category_match", "cause_keyword", "evidence_grounded", "no_secret_leak", "duration_s"]
    widths = {c: max(len(c), *(len(f"{r[c]}") for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(f"{r[c]}".ljust(widths[c]) for c in cols))
    n = len(rows)
    print("\nMoyennes : " + "  ".join(
        f"{c}={sum(r[c] for r in rows) / n:.2f}" for c in cols[2:6]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline-doctor-evals")
    parser.add_argument("--examples", default="examples")
    parser.add_argument("--langfuse", action="store_true", help="lancer aussi une experience Langfuse")
    parser.add_argument("--run-name", default=os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:3b"))
    parser.add_argument("--min-category", type=float, default=0.0,
                        help="seuil (0-1) de category_match moyen ; en dessous : code retour 1 (gate CI)")
    args = parser.parse_args(argv)

    items = load(args.examples)
    rows = run_local(items)
    print_table(rows)
    if args.langfuse:
        res = run_langfuse(items, args.run_name)
        print("\n" + res.format(), file=sys.stderr)
    mean_cat = sum(r["category_match"] for r in rows) / len(rows)
    leak = any(r["no_secret_leak"] < 1 for r in rows)
    if leak:
        print("ECHEC : un secret apparait dans une sortie.", file=sys.stderr)
    return 1 if leak or mean_cat < args.min_category else 0


if __name__ == "__main__":
    raise SystemExit(main())
