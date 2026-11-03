# Changelog

Format : [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) - versionnage [SemVer](https://semver.org/lang/fr/).

## [Unreleased]
### Added
- CLI : log -> sanitizer -> Ollama -> diagnostic (cause, preuves, correctif, confiance).
- Webhook GitHub Actions + notification Slack : workflow `workflow_run` sur echec de la CI, collecte du log des jobs en echec, nettoyage et notification via Incoming Webhook (ADR-0002).
- Agent auto-heberge : poll GitHub -> Ollama -> Slack, journal SQLite (idempotence), deploiement systemd/cron (ADR-0003).
- Sanitizer : masquage des secrets courants, retrait des codes ANSI, troncature (debut + fin).
- Gouvernance et securite : LICENSE, SECURITY.md, CONTRIBUTING.md, CODEOWNERS, Dependabot, CodeQL, gitleaks, pre-commit.
- Documentation : schema d'architecture, ADR-0001.
