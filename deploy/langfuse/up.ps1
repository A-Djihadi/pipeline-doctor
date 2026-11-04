# Demarre Langfuse auto-heberge (compose officiel) avec initialisation headless. Windows / PowerShell.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path docker-compose.yml)) {
  Invoke-WebRequest -UseBasicParsing -OutFile docker-compose.yml `
    https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml
}
docker compose up -d
Write-Host "Langfuse : http://localhost:3000  (identifiants : voir deploy/langfuse/.env)"
