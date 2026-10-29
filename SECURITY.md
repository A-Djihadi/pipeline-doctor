# Politique de securite

## Signaler une vulnerabilite

**Ne publiez pas de details d'une faille dans une issue ou une PR publique.**

Utilisez le signalement prive de GitHub :
onglet **Security** > **Report a vulnerability** (Private vulnerability reporting).

Delai cible : accuse de reception sous 7 jours, correctif ou plan de correction sous 30 jours.
Une fois le correctif publie, un security advisory (GHSA, et CVE si pertinent) est diffuse.

## Versions supportees

Projet en developpement initial : seule la branche `main` recoit des correctifs.

## Ce que le projet garantit (et ne garantit pas)

- L'agent est en **lecture seule** : il propose un diagnostic, il n'applique aucun changement.
- Un **sanitizer** masque les secrets courants avant tout envoi a un modele. Il repose sur des motifs :
  il ne remplace pas une gestion correcte des secrets en CI et peut rater un format inconnu.
  Signalez tout secret non masque via le canal prive ci-dessus.
- Les logs de build peuvent contenir des donnees sensibles : ne publiez jamais un log reel dans une issue
  sans l'avoir anonymise.
