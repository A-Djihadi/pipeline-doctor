# ADR-0004 : Escalade cloud (DeepSeek) si confiance sous le seuil

- Statut : accepte
- Date : 2026-11-03

## Contexte
La session 3 diagnostique chaque echec CI avec un modele local (Ollama). Un petit modele local
peut etre imprecis : son diagnostic est accompagne d'un score de confiance, mais ce score n'etait
jusqu'ici jamais exploite. L'ADR-0001 prevoyait deja qu'un modele cloud n'est sollicite que si la
confiance est basse (« Ollama d'abord »).

## Decision
1. **Provider cloud = DeepSeek** (API compatible OpenAI), appele via `urllib.request` (stdlib),
   comme `ask_ollama`. Claude reste une etape ulterieure, hors perimetre.
2. **Seuil de confiance** : variable `PD_CONFIDENCE_THRESHOLD`, defaut `0.6`.
3. **Endpoint/model** : `PD_DEEPSEEK_URL` (defaut `https://api.deepseek.com/chat/completions`),
   `PD_DEEPSEEK_MODEL` (defaut `deepseek-chat`).
4. **Politique d'escalade** (apres un `ask_ollama` reussi) :
   - confiance >= seuil -> on garde le diagnostic Ollama (`provider="ollama"`) ;
   - confiance < seuil et cle `PD_DEEPSEEK_API_KEY` presente -> `ask_deepseek` ; en cas de succes le
     diagnostic est remplace (`provider="deepseek"`), en cas d'echec on garde Ollama et on trace en
     stderr ;
   - confiance < seuil sans cle -> on garde Ollama, trace stderr, pas d'erreur ;
   - confiance absente/invalide -> traitee comme 0 (donc escalade si une cle est presente) ;
   - si `ask_ollama` echoue, comportement inchange (`status="llm_error"`, pas d'escalade).
5. **Tracabilite** : le journal SQLite gagne une colonne `provider` (defaut `"ollama"`), et le
   message Slack indique le fournisseur (`via DeepSeek` le cas echeant).

## Consequences
- Cout maitrise : DeepSeek n'est appele que pour les diagnostics locaux peu fiables, jamais pour le
  cas courant.
- Une cle `PD_DEEPSEEK_API_KEY` doit etre stockee hors du depot (environnement, chmod 600) ; son
  absence ne casse rien (escalade simplement ignoree).
- Le sanitizer reste obligatoire avant l'appel DeepSeek (meme pipeline `sanitize` que pour Ollama) :
  aucun secret ne part vers le cloud.
- Les anciennes bases SQLite sont migrees a chaud (`ALTER TABLE ADD COLUMN`, idempotent).
