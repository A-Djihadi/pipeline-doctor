# pipeline-doctor

[![CI](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/ci.yml)
[![CodeQL](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/codeql.yml/badge.svg)](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Agent qui lit le log d'un build CI en echec et propose un diagnostic : cause probable, preuves tirees du log, correctif suggere.

> Statut : session 4/5. Escalade cloud en place (DeepSeek si confiance < seuil). Le journal des diagnostics (UI) arrive ensuite.

![architecture](docs/architecture.png)
*(source modifiable : `docs/architecture.excalidraw`, a ouvrir sur excalidraw.com)*

## Principes
- **Lecture seule** : l'agent propose, il n'applique rien.
- **Secrets masques** avant tout envoi a un modele (tokens, mots de passe, cles AWS, URLs avec identifiants).
- **Local d'abord** : Ollama traite le cas courant, un modele cloud n'est sollicite que si la confiance est basse.
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

## Escalade cloud (DeepSeek)
Quand Ollama repond avec une confiance sous le seuil, l'agent re-diagnostique avec DeepSeek
(API compatible OpenAI) pour une reponse de meilleure qualite, sans jamais perdre le diagnostic
local en cas d'echec.

- Variables : `PD_CONFIDENCE_THRESHOLD` (defaut `0.6`), `PD_DEEPSEEK_API_KEY`,
  `PD_DEEPSEEK_URL` (defaut `https://api.deepseek.com/chat/completions`), `PD_DEEPSEEK_MODEL`
  (defaut `deepseek-chat`).
- Comportement : confiance >= seuil -> Ollama conserve ; confiance < seuil et cle presente ->
  diagnostic remplace (`provider=deepseek`) ; sans cle ou en cas d'echec -> Ollama conserve.
- Le message Slack indique le fournisseur (`Diagnostic (via DeepSeek, X%)`).
- Voir [ADR-0004](docs/adr/0004-escalade-cloud.md).

## Tests
```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Feuille de route
- [x] 1. CLI : log -> sanitizer -> Ollama -> diagnostic
- [x] 2. Webhook GitHub Actions + notification Slack (log nettoye, sans LLM)
- [x] 3. Agent auto-heberge : poll GitHub -> Ollama -> Slack (diagnostic continu)
- [x] 4. Escalade vers DeepSeek si confiance < seuil
- [ ] 5. Journal des diagnostics (UI), demo video, article
- [ ] v2. Support Jenkins, ouverture de PR avec validation humaine

## Securite et documentation
- Failles : voir [SECURITY.md](SECURITY.md) (signalement prive, pas d'issue publique).
- Contribuer : [CONTRIBUTING.md](CONTRIBUTING.md). Decisions d'architecture : [docs/adr/](docs/adr/). Historique : [CHANGELOG.md](CHANGELOG.md).
