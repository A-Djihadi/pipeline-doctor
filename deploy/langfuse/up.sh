#!/usr/bin/env bash
# Demarre Langfuse auto-heberge (compose officiel) avec initialisation headless.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
[ -f docker-compose.yml ] || curl -fsSL -o docker-compose.yml \
  https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml
docker compose up -d
echo "Langfuse : http://localhost:3000  (identifiants : voir deploy/langfuse/.env)"
