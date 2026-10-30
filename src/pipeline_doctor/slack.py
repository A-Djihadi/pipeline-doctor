"""Notification Slack : construction du payload (Block Kit) et envoi via webhook.

stdlib uniquement. On n'envoie que du texte deja nettoye par le sanitizer.
"""
import json
import urllib.request

# Slack plafonne chaque champ texte de block a 3000 caracteres. On garde de la
# marge pour les fences ``` et le texte d'en-tete.
MAX_BLOCK_CHARS = 2800


def build_payload(repo, run_name, branch, run_id, run_url, n_redacted, sanitized):
    """Construit le payload Block Kit pour un Incoming Webhook.

    `sanitized` est le log deja nettoye ; on ne garde que la fin, la ou se
    trouve l'erreur, pour tenir dans un seul bloc Slack.
    """
    excerpt = sanitized[-MAX_BLOCK_CHARS:]
    return {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"\U0001F534 CI en echec : {repo}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Workflow*\n{run_name}"},
                    {"type": "mrkdwn", "text": f"*Branche*\n{branch}"},
                    {"type": "mrkdwn", "text": f"*Secrets masques*\n{n_redacted}"},
                    {"type": "mrkdwn", "text": f"*Run*\n<{run_url}|#{run_id}>"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Extrait du log (nettoye, fin du log)*"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```{excerpt}```"},
            },
        ]
    }


def send(webhook_url: str, payload: dict, timeout: int = 30) -> int:
    """POST le payload vers l'Incoming Webhook, retourne le code HTTP."""
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status
