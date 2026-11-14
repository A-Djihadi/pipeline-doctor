# Changelog

Format : [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) - versionnage [SemVer](https://semver.org/lang/fr/).

## [Unreleased]
### Changed
- Re-essai sur doute et plus seulement sur confiance basse : preuve citee absente du log ou categorie du modele differente de celle des regles (`doubts`, `retry_reasons`, score `category_agreement`). Le prompt demande la categorie de la cause racine et interdit les contournements (`--force`, tests desactives...).
- Evaluation durcie : `category_match` juge la categorie du modele (plus celle des regles), `cause_keyword` refuse les mots interdits et ignore les accents, nouvel evaluateur `safe_fix`, 3 logs pieges (disque plein, variable d'environnement manquante dans les tests, timeout d'API dans les tests). Regle `disk`.
- `python -m pipeline_doctor.tracing` synchronise le prompt dans Langfuse sans creer de version en double.
- Schema d'architecture regenere (sans l'escalade cloud retiree par l'ADR-0004, avec les cas d'utilisation) ; schema du graphe mis a jour.

### Added
- v2 : source Jenkins (lecture seule, ADR-0007) et correctifs proposes — mode `propose` (diff garde poste sur Slack, aucune ecriture) et mode `pr` (PR *draft* depuis un fork bot, jamais mergee par l'agent, ADR-0008) ; retro-compatibilite totale par defaut (ADR-0009).
- Mode graphe LangGraph (`graph.py`) : sanitize -> classify -> diagnose, re-essai a contexte resserre si confiance basse, repli par regles si Ollama est indisponible, validation humaine (`--review`, interrupt + checkpointer), schema Mermaid (ADR-0005).
- Observabilite Langfuse auto-hebergee (`tracing.py`, `deploy/langfuse/`) : span par noeud, generation par appel Ollama (tokens, modele, prompt versionne), scores `confidence` / `evidence_grounded` / `source`, sanitizer branche en `mask`.
- Evaluation (`evals.py`, `examples/expected.json`, 5 nouveaux logs) : evaluateurs deterministes, rapport local ou experience Langfuse ; gate CI sur les fuites de secrets.
- Classification par regles (`rules.py`) et agent `PD_ENGINE=graph`.
- CLI : log -> sanitizer -> Ollama -> diagnostic (cause, preuves, correctif, confiance).
- Webhook GitHub Actions + notification Slack : workflow `workflow_run` sur echec de la CI, collecte du log des jobs en echec, nettoyage et notification via Incoming Webhook (ADR-0002).
- Agent auto-heberge : poll GitHub -> Ollama -> Slack, journal SQLite (idempotence), deploiement systemd/cron (ADR-0003).
- Sanitizer : masquage des secrets courants, retrait des codes ANSI, troncature (debut + fin).
- Gouvernance et securite : LICENSE, SECURITY.md, CONTRIBUTING.md, CODEOWNERS, Dependabot, CodeQL, gitleaks, pre-commit.
- Documentation : schema d'architecture, ADR-0001.

### Security
- Le log brut ne transite plus par l'etat du graphe (contexte d'execution LangGraph) : il n'apparait plus en entree des spans Langfuse, meme sans le `mask`.
- `evals --langfuse` pousse dans le dataset un log nettoye par le sanitizer (le `mask` ne couvre pas les datasets) ; le log brut reste local pour l'execution et l'evaluateur `no_secret_leak`.

### Removed
- Escalade cloud (DeepSeek / Claude) retiree : decision « Ollama only », aucune cle API cloud (ADR-0004, revert PR #5).
