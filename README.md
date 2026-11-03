# pipeline-doctor

[![CI](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/ci.yml)
[![CodeQL](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/codeql.yml/badge.svg)](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Agent qui lit le log d'un build CI en echec et propose un diagnostic : cause probable, preuves tirees du log, correctif suggere.

> Statut : sessions 1-3 faites (CLI, webhook Slack, agent auto-heberge). Decision : Ollama only — aucune cle API cloud, pas d'escalade (ADR-0004).

![architecture](docs/architecture.png)
*(source modifiable : `docs/architecture.excalidraw`, a ouvrir sur excalidraw.com)*

## Principes
- **Lecture seule** : l'agent propose, il n'applique rien.
- **Secrets masques** avant tout envoi a un modele (tokens, mots de passe, cles AWS, URLs avec identifiants).
- **Local only** : Ollama traite tous les diagnostics, aucun modele cloud (ni DeepSeek, ni Claude), aucune cle API cloud.
- **Zero dependance** : bibliotheque standard Python uniquement.

## Utilisation
```bash
# Prerequis : Ollama lance, avec un modele (defaut : qwen2.5-coder:3b)
export PYTHONPATH=src
python -m pipeline_doctor examples/maven-compile-error.log             # diagnostic
python -m pipeline_doctor examples/maven-compile-error.log --dry-run   # voir ce qui serait envoye
cat build.log | python -m pipeline_doctor -                            # depuis stdin
```
Variables : `OLLAMA_URL` (defaut `http://localhost:11434`), `OLLAMA_MODEL`.

## Webhook GitHub Actions + Slack
Quand la CI (`ci.yml`) echoue, le workflow [`diagnose.yml`](.github/workflows/diagnose.yml) se declenche
automatiquement (evenement `workflow_run`), recupere le log des jobs en echec, le nettoie (sanitizer)
et poste un extrait sur Slack via un Incoming Webhook.

- Secret requis : `SLACK_WEBHOOK_URL` (URL de l'Incoming Webhook, dans Settings > Secrets and variables > Actions).
- Sans ce secret (fork, PR externe), la notification est ignoree sans faire echouer le run.
- Test manuel : `gh workflow run diagnose.yml -f run_id=<id-du-run>` (voir le payload sans poster : `--dry-run`).
- Voir [ADR-0002](docs/adr/0002-webhook-notification-slack.md).

## Agent auto-heberge (diagnostic LLM local)
Un service autonome sur ton serveur Ollama poll l'API GitHub, diagnostique les runs en echec avec
un modele local et notifie Slack (cause, confiance, correctif). Idempotent via un journal SQLite :
un run est traite une seule fois.

- Lancer : `PD_REPO=... GITHUB_TOKEN=... python -m pipeline_doctor.agent --once` (ou `--interval 300`).
- Voir [deploy/](deploy/) (systemd + cron) et [ADR-0003](docs/adr/0003-agent-auto-heberge.md).

## Tests
```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Feuille de route
- [x] 1. CLI : log -> sanitizer -> Ollama -> diagnostic
- [x] 2. Webhook GitHub Actions + notification Slack (log nettoye, sans LLM)
- [x] 3. Agent auto-heberge : poll GitHub -> Ollama -> Slack (diagnostic continu)
- [x] ~~4. Escalade vers DeepSeek / Claude si confiance < seuil~~ *(decision : Ollama only, pas d'escalade cloud — ADR-0004)*
- [ ] 5. Journal des diagnostics (UI), demo video, article
- [ ] v2. Support Jenkins, ouverture de PR avec validation humaine

## Securite et documentation
- Failles : voir [SECURITY.md](SECURITY.md) (signalement prive, pas d'issue publique).
- Contribuer : [CONTRIBUTING.md](CONTRIBUTING.md). Decisions d'architecture : [docs/adr/](docs/adr/). Historique : [CHANGELOG.md](CHANGELOG.md).
