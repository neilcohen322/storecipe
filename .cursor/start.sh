#!/usr/bin/env bash
# Per-boot startup for the Storecipe backend stack. Starts the nested Docker
# daemon, brings up the Compose services, and applies database migrations in
# dependency order. Idempotent and safe to re-run; returns once the stack is up.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\n=== %s ===\n' "$*"; }

DC="sudo docker compose"

# --- Ensure the Docker daemon is running ------------------------------------
if ! sudo docker info >/dev/null 2>&1; then
  log "Starting Docker daemon"
  sudo service docker start || true
  for _ in $(seq 1 60); do
    if sudo docker info >/dev/null 2>&1; then break; fi
    sleep 1
  done
fi
sudo docker info >/dev/null 2>&1 || { echo "ERROR: Docker daemon failed to start"; exit 1; }

# --- Bring up data stores first, then apply migrations ----------------------
log "Starting data stores"
$DC up -d --build postgres redis redis-broker

log "Waiting for PostgreSQL to become healthy"
for _ in $(seq 1 60); do
  status="$(sudo docker inspect -f '{{.State.Health.Status}}' storecipe-postgres-1 2>/dev/null || echo starting)"
  [ "$status" = "healthy" ] && break
  sleep 2
done

log "Applying Catalog migrations"
$DC run --rm --no-deps -w /app/services/catalog --entrypoint alembic catalog-api upgrade head

log "Applying Ingestion migrations"
$DC run --rm --no-deps -w /app/services/ingestion --entrypoint alembic ingestion-api upgrade head

# --- Start the full stack ---------------------------------------------------
log "Starting all services"
$DC up -d

log "start.sh complete"
$DC ps
