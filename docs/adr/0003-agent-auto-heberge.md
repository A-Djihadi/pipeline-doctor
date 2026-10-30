# ADR-0003 : Agent auto-heberge (poll GitHub -> Ollama -> Slack)

- Statut : accepte
- Date : 2026-10-30

## Contexte
La session 2 notifie un echec CI mais ne le diagnostique pas. Le but de la session 3 est le
diagnostic automatique par modele local (Ollama), « industrialise » : un service qui tourne en
continu sur l'infra de Djihadi, sans dependre du cloud ni d'une intervention manuelle.

Contrainte : GitHub Actions tourne dans le cloud et ne peut pas joindre l'Ollama local. Un
tunnel ou un runner qui embarque Ollama sont possibles mais fragiles / non « on-prem ».

## Decision
1. **Agent auto-heberge** (`agent.py`) qui **poll** l'API GitHub (pull), pas de webhook entrant :
   il fonctionne derriere NAT, sans exposition reseau, sur le serveur Ollama ou un futur VPS.
2. **Boucle** : lister les runs du workflow CI -> filtrer `conclusion == failure` -> recuperer le
   log des jobs en echec -> sanitizer -> diagnostic Ollama -> notification Slack.
3. **Journal SQLite** (idempotence) : un run est traite une seule fois ; l'historique est la base
   de la session 4 (journal des diagnostics).
4. **Resilience** : echec du LLM -> on notifie quand meme « diagnostic indisponible » et on
   journalise `llm_error` ; sans `SLACK_WEBHOOK_URL`, on n'echoue pas.
5. **Declencheurs** : `--once` (cron) ou `--interval` (service systemd). Le workflow GitHub
   `diagnose.yml` (session 2) reste un fallback sans LLM, non utilise par defaut en production.

## Consequences
- Un token lecture seule (PAT `Actions: read`) doit etre present sur le serveur, stocke hors du
  depot (fichier d'environnement, chmod 600).
- Latence = frequence de poll : compromis entre fraicheur de l'alerte et volume d'appels API.
- Le diagnostic ne depend d'aucun modele cloud ; l'escalade DeepSeek / Claude (confiance basse)
  reste une etape ulterieure, conforme a ADR-0001 (« Ollama d'abord »).
