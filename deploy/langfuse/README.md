# Langfuse auto-heberge

Langfuse est la « tour de controle » des appels LLM : chaque diagnostic devient une **trace**
(un span par noeud LangGraph, une *generation* par appel Ollama, des *scores*). Il tourne en
local a cote d'Ollama : aucune donnee ne quitte la machine (coherent avec ADR-0004).

```bash
./deploy/langfuse/up.sh            # Linux / macOS / WSL
.\deploy\langfuse\up.ps1           # Windows PowerShell (Docker Desktop)
```

Le script recupere le `docker-compose.yml` officiel (web, worker, Postgres, ClickHouse, Redis,
MinIO) et l'initialise sans clic grace aux variables `LANGFUSE_INIT_*` de `.env` : organisation,
projet et cles API `pk-lf-local-dev` / `sk-lf-local-dev` sont crees au premier demarrage.

Puis, a la racine du depot :

```bash
pip install -r requirements-agent.txt
export LANGFUSE_BASE_URL=http://localhost:3000
export LANGFUSE_PUBLIC_KEY=pk-lf-local-dev LANGFUSE_SECRET_KEY=sk-lf-local-dev
PYTHONPATH=src python -m pipeline_doctor.graph examples/maven-compile-error.log
PYTHONPATH=src python -m pipeline_doctor.evals --langfuse     # dataset + experience
```

| Composant  | Role                                                        |
|------------|-------------------------------------------------------------|
| web        | UI + API d'ingestion (port 3000)                            |
| worker     | traitement asynchrone des evenements                        |
| Postgres   | metadonnees (projets, prompts, datasets, utilisateurs)      |
| ClickHouse | stockage analytique des traces / observations / scores      |
| Redis      | file d'attente et cache                                     |
| MinIO (S3) | evenements bruts et medias                                  |

En production (ex. Azure) : Postgres -> Azure Database for PostgreSQL, Redis -> Azure Cache for
Redis, S3 -> Blob Storage, web/worker sur AKS (chart Helm officiel) ; ClickHouse reste a operer.
