# ADR-0001 : Architecture initiale de pipeline-doctor

- Statut : accepte
- Date : 2026-10-29

## Contexte
Diagnostiquer un build CI en echec a partir de son log, sans exposer de secrets et sans cout recurrent.
Le depot est public : tout ce qui est envoye a un modele ou commite doit etre sans donnee sensible.

## Decision
1. **Lecture seule** : l'agent propose un diagnostic (cause, preuves, correctif, confiance) et n'applique rien.
2. **Sanitizer obligatoire** avant tout envoi a un modele : masquage des secrets, retrait ANSI, troncature debut + fin.
3. **Local d'abord** : Ollama traite le cas courant ; un modele cloud (DeepSeek / Claude) n'est sollicite que si la confiance est sous un seuil.
4. **Zero dependance** : bibliotheque standard Python uniquement (surface d'attaque supply-chain minimale).
5. **Livraison par etapes** : CLI -> webhook GitHub Actions + Slack -> escalade cloud -> journal des diagnostics.

Schema : [architecture.png](../architecture.png) (source : `architecture.excalidraw`).

## Consequences
- Un faux negatif du sanitizer peut laisser fuiter un secret vers un modele cloud : l'escalade cloud ne s'active qu'apres `--dry-run` possible et tests dedies.
- Un modele local de petite taille peut etre moins precis : d'ou le score de confiance et l'escalade.
- Pas de dependance a gerer, mais plus de code maison a maintenir (client HTTP, parsing).
