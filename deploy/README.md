# Deployer l'agent auto-heberge

L'agent `pipeline_doctor.agent` tourne sur ton serveur (celui qui heberge Ollama, ou un futur
VPS). Il poll l'API GitHub pour les runs CI en echec, diagnostique via Ollama et notifie Slack.

## 1. Preparer le serveur

```bash
git clone https://github.com/A-Djihadi/pipeline-doctor.git /opt/pipeline-doctor
cd /opt/pipeline-doctor
# verifier que Ollama repond
curl -s http://localhost:11434/api/tags | head
```

## 2. Token GitHub (lecture seule)

Cree un *fine-grained personal access token* :
- Settings > Developer settings > Personal access tokens > Fine-grained tokens.
- Repository : `A-Djihadi/pipeline-doctor`.
- Permissions : **Actions : Read-only**, **Contents : Read-only** (ou Metadata : Read-only).

Ne le committe jamais.

## 3. Fichier d'environnement (secrets)

```bash
sudo tee /etc/pipeline-doctor.env > /dev/null <<'EOF'
PD_REPO=A-Djihadi/pipeline-doctor
PD_WORKFLOW=ci.yml
GITHUB_TOKEN=github_pat_XXXX
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:3b
EOF
sudo chmod 600 /etc/pipeline-doctor.env
```

`SLACK_WEBHOOK_URL` est optionnel : sans lui, l'agent journalise mais ne notifie pas.

## 4a. Service systemd (boucle continue)

```bash
# ajuster User= et WorkingDirectory= dans deploy/pipeline-doctor.service
sudo cp deploy/pipeline-doctor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pipeline-doctor
sudo journalctl -u pipeline-doctor -f
```

## 4b. Alternative : cron (un passage a la fois)

```bash
sudo tee /etc/cron.d/pipeline-doctor > /dev/null <<'EOF'
*/5 * * * * root cd /opt/pipeline-doctor && . /etc/pipeline-doctor.env && \
  PYTHONPATH=src /usr/bin/python3 -m pipeline_doctor.agent --once >> /var/log/pipeline-doctor.log 2>&1
EOF
```

## 5. Verifier

```bash
cd /opt/pipeline-doctor
. /etc/pipeline-doctor.env
PYTHONPATH=src python3 -m pipeline_doctor.agent --once --dry-run   # voir sans rien envoyer
```

Le journal des diagnostics est dans la base `PD_DB` (defaut `diagnostics.db`).
