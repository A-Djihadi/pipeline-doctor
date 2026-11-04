# ADR-0005 : Orchestration LangGraph et observabilite Langfuse (extra optionnel)

- Statut : accepte
- Date : 2026-11-04

## Contexte
Le diagnostic de la session 3 est une fonction lineaire : log -> sanitizer -> Ollama -> Slack.
Trois besoins ne rentrent plus dans ce modele :
1. **Decisions** : re-essayer avec un contexte resserre si la confiance est basse, se replier
   sur des regles si Ollama est indisponible, demander une validation humaine (v2 : PR).
2. **Observabilite** : en run, on doit pouvoir repondre a « pourquoi ce diagnostic ? », « combien
   de tokens / quelle latence ? », « quelle version du prompt ? », « le modele invente-t-il des
   preuves ? ». Les logs applicatifs ne suffisent pas pour un composant probabiliste.
3. **Qualite mesuree** : changer de prompt ou de modele doit se juger sur un jeu d'evaluation,
   pas a l'intuition.

## Decision
1. **LangGraph** orchestre le diagnostic (`graph.py`) : etat type `DiagState`, un noeud par
   etape, aretes conditionnelles explicites (`route_after_diagnose`), `interrupt()` +
   checkpointer pour la validation humaine. Nombre d'essais LLM borne (`MAX_ATTEMPTS = 2`).
2. **Langfuse auto-heberge** (`deploy/langfuse/`) trace chaque execution : span par noeud
   (CallbackHandler LangChain), *generation* par appel Ollama (modele, tokens, prompt), scores
   `confidence`, `evidence_grounded`, `source`. Le prompt systeme est versionne dans Langfuse
   (label `production`) avec repli sur le prompt du code.
3. **Evaluation** (`evals.py`) : dataset `examples/` + `expected.json`, evaluateurs
   deterministes (categorie, mots-cles, ancrage des preuves, absence de fuite de secret),
   executable en local ou comme *experience* Langfuse.
4. **Extra optionnel** : `requirements-agent.txt`. Sans lui, la CLI, le webhook et l'agent des
   sessions 1-3 restent 100 % stdlib (ADR-0001 tenu). Sans cles Langfuse, le tracing est un no-op.

## Consequences
- ADR-0004 tenu : Langfuse et Ollama sont locaux, aucun log ne quitte la machine. Le sanitizer
  est en plus branche comme `mask` du client Langfuse (defense en profondeur). Le log brut
  passe par le contexte d'execution LangGraph, pas par l'etat : le CallbackHandler, qui trace
  l'etat en entree de chaque noeud, ne le voit jamais. Le `mask` ne couvre pas les datasets :
  `evals.py` y pousse un log deja nettoye.
- Deux dependances (versions epinglees, Dependabot) pour le mode graphe ; la CI teste les deux
  modes (stdlib seul, et avec l'extra).
- Langfuse auto-heberge = 6 conteneurs (web, worker, Postgres, ClickHouse, Redis, MinIO) : cout
  d'exploitation accepte pour la confidentialite ; alternative cloud rejetee (ADR-0004).
