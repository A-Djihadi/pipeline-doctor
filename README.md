# pipeline-doctor

[![CI](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/ci.yml)
[![CodeQL](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/codeql.yml/badge.svg)](https://github.com/A-Djihadi/pipeline-doctor/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Agent qui lit le log d'un build CI en echec et propose un diagnostic : cause probable, preuves tirees du log, correctif suggere.

> Statut : session 1/4. Le diagnostic local fonctionne en ligne de commande. Webhook GitHub, Slack et escalade cloud arrivent ensuite.

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

## Tests
```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Feuille de route
- [x] 1. CLI : log -> sanitizer -> Ollama -> diagnostic
- [ ] 2. Webhook GitHub Actions + notification Slack
- [ ] 3. Escalade vers DeepSeek / Claude si confiance < seuil
- [ ] 4. Journal des diagnostics, demo video, article
- [ ] v2. Support Jenkins, ouverture de PR avec validation humaine

## Securite et documentation
- Failles : voir [SECURITY.md](SECURITY.md) (signalement prive, pas d'issue publique).
- Contribuer : [CONTRIBUTING.md](CONTRIBUTING.md). Decisions d'architecture : [docs/adr/](docs/adr/). Historique : [CHANGELOG.md](CHANGELOG.md).
