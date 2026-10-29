# Contribuer

## Avant de proposer une PR
- Une PR = un sujet. Messages de commit au format [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `ci:`...).
- Tests : `PYTHONPATH=src python -m unittest discover -s tests -v`
- **Aucun secret, aucune donnee perso ou client** dans le code, les exemples ou les logs. Les exemples utilisent des valeurs factices.
- Un changement de comportement met a jour la documentation dans la meme PR (README, `docs/`, `CHANGELOG.md`).
- Une decision d'architecture = un ADR dans `docs/adr/`.

## Hooks locaux (recommandes)
```bash
pip install pre-commit && pre-commit install
```
Le hook gitleaks bloque un commit contenant un secret.

## Securite
Voir [SECURITY.md](SECURITY.md). Les failles se signalent en prive.
