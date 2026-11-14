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
  `Contents : Read-only` est requis en plus pour le mode `propose` des correctifs (l'agent lit
  l'arbre et les fichiers du commit en echec).

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

## 6. Source Jenkins (lecture seule, ADR-0007)

Cree un utilisateur Jenkins dedie avec uniquement `Overall/Read` + `Job/Read` sur les jobs
surveilles, puis genere un token API pour cet utilisateur. Stocke-les dans
`/etc/pipeline-doctor.env` (chmod 600) :

```bash
PD_JENKINS_URL=https://jenkins.example.com
PD_JENKINS_JOBS=team/app/main
PD_JENKINS_USER=pd-readonly
PD_JENKINS_TOKEN=<api-token>
```

L'agent ne fait que des `GET` (liste des builds, `consoleText`) ; il ne declenche, n'annule ni ne
configure jamais rien.

## 7. Correctifs proposes : mode `pr` (ADR-0008, actions owner)

Le mode `propose` ne demande que `Contents : Read-only` (section 2). Le mode `pr` demande en plus,
dans cet ordre :

3. Creer un *machine user* (ex. `pd-bot`), avec 2FA, possede par le mainteneur.
4. Forker le depot surveille dans le compte du machine user. **Ne pas** ajouter le machine user
   comme collaborateur de l'upstream.
5. Creer un *classic PAT* pour le machine user, scope `public_repo` uniquement, expiration 90 jours ;
   le stocker comme `PD_FIX_TOKEN` dans `/etc/pipeline-doctor.env`. Regler `PD_FIX_FORK=pd-bot/<repo>`.
6. Upstream > Settings > Actions > General > Fork pull request workflows :
   **Require approval for all external contributors**.
7. Ruleset upstream sur la branche par defaut : PR requise, >= 1 approbation, approbations obsoletes
   rejetees, checks requis (`test`, `test-agent-extra`, `secrets-scan`), pas de force-push ni de
   suppression, liste de bypass vide sauf le proprietaire (baseline DevSecOps).
8. Verifier une fois avec `PD_FIX_MODE=pr python -m pipeline_doctor.agent --once --dry-run`, puis un
   vrai passage sur une branche volontairement cassee.
