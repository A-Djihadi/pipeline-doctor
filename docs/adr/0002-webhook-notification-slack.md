# ADR-0002 : Webhook GitHub Actions + notification Slack (sans LLM)

- Statut : accepte
- Date : 2026-10-30

## Contexte
Un diagnostic n'a de valeur que s'il arrive au moment ou le build casse, sans action manuelle.
La session 2 relie donc la CI a une notification. Le passage par un modele (Ollama / cloud)
reste la session 3.

## Decision
1. **Declencheur = evenement `workflow_run`** (webhook natif de GitHub Actions) sur le workflow
   `CI`, filtre `conclusion == failure`. Pas de webhook HTTP externe ni d'infra en plus : le
   workflow tourne dans le depot, avec un `GITHUB_TOKEN` limite a `actions: read`.
2. **Collecte** : liste des jobs du run via l'API, recuperation du log des jobs en echec
   (endpoint `/actions/jobs/{id}/logs`, texte brut).
3. **Sanitizer obligatoire** avant tout envoi : meme `sanitize()` qu'en session 1 (secrets
   masques, ANSI retire, troncature), plafonne ici a 12000 caracteres.
4. **Notification Slack** : Incoming Webhook (secret GitHub `SLACK_WEBHOOK_URL`), payload
   Block Kit : workflow, branche, lien du run, nombre de secrets masques, extrait du log
   nettoye (fin du log, la ou est l'erreur).
5. **Pas de LLM** en session 2 : on notifie le log nettoye ; le diagnostic par modele arrive
   en session 3.
6. **Degradation douce** : sans `SLACK_WEBHOOK_URL` (fork, PR externe) ou sans job en echec,
   le run se termine sans erreur.

## Consequences
- La notification depend d'un secret (webhook) : indisponible sur les PR de forks (limite
  GitHub sur `workflow_run`), donc pas de fuite possible, mais pas de notification sur ces PR.
- Deux workflows lies (`CI` -> `diagnose`) ; le second s'execute toujours depuis la version
  de `main`.
- L'extrait Slack est tronque a la fin du log (la ou est l'erreur) ; le log complet reste sur
  la page du run.
