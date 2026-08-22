#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [[ $EUID -ne 0 ]]; then
  echo "deploy.sh must run as root" >&2
  exit 1
fi
if [[ $# -ne 1 ]]; then
  echo "usage: deploy.sh /path/to/release-manifest.json" >&2
  exit 2
fi

ROOT_DIR=${STORECIPE_ROOT:-/opt/storecipe/current}
COMPOSE_FILE=${STORECIPE_COMPOSE_FILE:-$ROOT_DIR/compose.yaml}
TARGET_MANIFEST=$(readlink -f "$1")
CURRENT_MANIFEST=$ROOT_DIR/release-manifest.json
PREVIOUS_MANIFEST=/opt/storecipe/releases/previous-release-manifest.json
RUNTIME_ENV=/run/storecipe/runtime.env
TARGET_ENV=/run/storecipe/target-release.env
PREVIOUS_ENV=/run/storecipe/previous-release.env
LOCK_FILE=/var/lock/storecipe-deploy.lock
DISK_REQUIRED_KB=$((5 * 1024 * 1024))
APP_SERVICES=(edge catalog-api ingestion-api ingestion-worker ingestion-dispatcher ingestion-reconciler mcp-gateway)
ALL_SERVICES=(postgres redis-cache redis-broker "${APP_SERVICES[@]}")
STACK_CHANGED=0
MIGRATIONS_APPLIED=none

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another Storecipe deployment is already running" >&2; exit 1; }

cleanup() {
  rm -f "$RUNTIME_ENV" "$TARGET_ENV" "$PREVIOUS_ENV"
}
trap cleanup EXIT

compose() {
  docker compose --env-file "$ACTIVE_ENV" -f "$COMPOSE_FILE" "$@"
}

append_manifest_images() {
  local manifest=$1 destination=$2
  {
    printf 'STORECIPE_WEB_IMAGE=%s\n' "$(jq -r '.images.web' "$manifest")"
    printf 'STORECIPE_CATALOG_IMAGE=%s\n' "$(jq -r '.images.catalog' "$manifest")"
    printf 'STORECIPE_INGESTION_IMAGE=%s\n' "$(jq -r '.images.ingestion' "$manifest")"
    printf 'STORECIPE_MCP_IMAGE=%s\n' "$(jq -r '.images.mcp' "$manifest")"
  } >> "$destination"
}

wait_for_healthy() {
  local deadline=$((SECONDS + 240)) service container status
  while (( SECONDS < deadline )); do
    local pending=0
    for service in "${ALL_SERVICES[@]}"; do
      container=$(compose ps -q "$service")
      if [[ -z $container ]]; then pending=1; continue; fi
      status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")
      if [[ $status == unhealthy || $status == exited || $status == dead ]]; then
        echo "Service failed readiness: $service ($status)" >&2
        return 1
      fi
      [[ $status == healthy || $status == running ]] || pending=1
    done
    (( pending == 0 )) && return 0
    sleep 5
  done
  echo "Timed out waiting for Storecipe services to become healthy" >&2
  return 1
}

wait_for_local_https() {
  local deadline=$((SECONDS + 180))
  while (( SECONDS < deadline )); do
    if curl --fail --silent --resolve "$PUBLIC_HOST:443:127.0.0.1" \
      "https://$PUBLIC_HOST/" --output /dev/null; then
      return 0
    fi
    sleep 5
  done
  echo "Local HTTPS routing for $PUBLIC_HOST is not ready" >&2
  curl --fail --show-error --resolve "$PUBLIC_HOST:443:127.0.0.1" \
    "https://$PUBLIC_HOST/" --output /dev/null
}

wait_for_service_health() {
  local service=$1 deadline=$((SECONDS + 120)) container status
  while (( SECONDS < deadline )); do
    container=$(compose ps -q "$service")
    if [[ -n $container ]]; then
      status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")
      [[ $status == healthy || $status == running ]] && return 0
      [[ $status == unhealthy || $status == exited || $status == dead ]] && break
    fi
    sleep 2
  done
  echo "Timed out waiting for $service to become healthy" >&2
  return 1
}

rollback_images() {
  [[ -f $PREVIOUS_MANIFEST ]] || {
    echo "No previous image manifest exists; initial deployment cannot be image-rolled back" >&2
    return 1
  }
  cp "$RUNTIME_ENV" "$PREVIOUS_ENV"
  append_manifest_images "$PREVIOUS_MANIFEST" "$PREVIOUS_ENV"
  chmod 0600 "$PREVIOUS_ENV"
  ACTIVE_ENV=$PREVIOUS_ENV
  export ACTIVE_ENV
  echo "Target failed after startup; restoring previous application image digests" >&2
  compose up -d --no-deps --force-recreate "${APP_SERVICES[@]}"
  wait_for_healthy
  echo "Previous application images are healthy. Database revisions were left unchanged." >&2
}

on_error() {
  local status=$?
  trap - ERR
  set +e
  if (( STACK_CHANGED == 1 )); then rollback_images; fi
  exit "$status"
}
trap on_error ERR

run_step() {
  local label=$1
  shift
  echo "==> $label"
  "$@"
}

migration_failed() {
  local service=$1
  echo "CRITICAL: $service migration failed after the pre-deployment backup." >&2
  echo "Database migration state may be partial (completed: $MIGRATIONS_APPLIED)." >&2
  echo "Do not retry deployment or start the target stack. Restore the latest pre-deployment backup, verify it, then investigate the migration failure." >&2
  return 1
}

for command in docker gcloud jq python3 flock curl swapon df; do
  command -v "$command" >/dev/null || { echo "Required command is missing: $command" >&2; exit 1; }
done
[[ -f $TARGET_MANIFEST && -f $COMPOSE_FILE ]] || { echo "Release manifest or Compose bundle is missing" >&2; exit 1; }
python3 "$ROOT_DIR/scripts/release/validate_manifest.py" "$TARGET_MANIFEST"

available_kb=$(df -Pk /var/lib/storecipe | awk 'NR==2 {print $4}')
(( available_kb >= DISK_REQUIRED_KB )) || { echo "At least 5 GiB free is required" >&2; exit 1; }
swapon --show=NAME --noheadings | grep -q . || { echo "Active swap is required" >&2; exit 1; }

source /etc/storecipe-host.conf
: "${RUNTIME_SECRET_NAME:?RUNTIME_SECRET_NAME is required}"
install -d -m 0750 -o root -g root /run/storecipe /opt/storecipe/releases
gcloud secrets versions access latest --secret "$RUNTIME_SECRET_NAME" > "$RUNTIME_ENV"
chmod 0600 "$RUNTIME_ENV"
if grep -Eqv '^(|#[^[:cntrl:]]*|[A-Z][A-Z0-9_]*=[A-Za-z0-9_./:@,+={}\[\]-]*)$' "$RUNTIME_ENV"; then
  echo "Runtime bundle contains unsupported or shell-sensitive syntax" >&2
  exit 1
fi

required_names=(
  GCP_BACKUP_BUCKET POSTGRES_ADMIN_USER POSTGRES_ADMIN_PASSWORD CATALOG_DB_PASSWORD
  INGESTION_DB_PASSWORD CATALOG_DATABASE_URL INGESTION_DATABASE_URL
  INGESTION_PAYLOAD_ACTIVE_KEY_ID INGESTION_PAYLOAD_KEYRING PUBLIC_ORIGIN PUBLIC_HOST
  CATALOG_M2M_TOKEN_URL CATALOG_M2M_CLIENT_ID CATALOG_M2M_CLIENT_SECRET
  AUTH0_ISSUER AUTH0_AUDIENCE OPENROUTER_API_KEY CATALOG_MEDIA_BUCKET
  MCP_RESOURCE_URL MCP_OBO_CLIENT_ID MCP_OBO_CLIENT_SECRET
)
for name in "${required_names[@]}"; do
  grep -qE "^${name}=.+" "$RUNTIME_ENV" || {
    echo "Runtime bundle is missing required variable: $name" >&2
    exit 1
  }
done
for name in STORECIPE_WEB_IMAGE STORECIPE_CATALOG_IMAGE STORECIPE_INGESTION_IMAGE STORECIPE_MCP_IMAGE; do
  ! grep -qE "^${name}=" "$RUNTIME_ENV" || {
    echo "Runtime bundle must not define release-controlled variable: $name" >&2
    exit 1
  }
done

set -a
# This root-created Secret Manager payload is deleted by the EXIT trap.
# shellcheck disable=SC1090
source "$RUNTIME_ENV"
set +a

manifest_origin=$(jq -r '.public.origin' "$TARGET_MANIFEST")
manifest_audience=$(jq -r '.public.api_audience' "$TARGET_MANIFEST")
manifest_mcp=$(jq -r '.public.mcp_resource_url' "$TARGET_MANIFEST")
manifest_auth0_domain=$(jq -r '.public.auth0_domain' "$TARGET_MANIFEST")
manifest_host=${manifest_origin#https://}
[[ $PUBLIC_ORIGIN == "$manifest_origin" && $PUBLIC_HOST == "$manifest_host" \
  && $AUTH0_ISSUER == "https://$manifest_auth0_domain/" \
  && $AUTH0_AUDIENCE == "$manifest_audience" && $MCP_RESOURCE_URL == "$manifest_mcp" ]] || {
  echo "Runtime public identifiers do not match the release manifest" >&2
  exit 1
}

if [[ -f $CURRENT_MANIFEST ]]; then
  jq -e --slurpfile previous "$CURRENT_MANIFEST" '
    .public == $previous[0].public and
    .migrations.catalog >= $previous[0].migrations.catalog and
    .migrations.ingestion >= $previous[0].migrations.ingestion
  ' "$TARGET_MANIFEST" >/dev/null || {
    echo "Deployment changes public contracts or requests an older database revision" >&2
    exit 1
  }
  install -m 0600 "$CURRENT_MANIFEST" "$PREVIOUS_MANIFEST"
fi

cp "$RUNTIME_ENV" "$TARGET_ENV"
append_manifest_images "$TARGET_MANIFEST" "$TARGET_ENV"
chmod 0600 "$TARGET_ENV"
set -a
# This file adds validated digest references to the fetched runtime bundle.
# shellcheck disable=SC1090
source "$TARGET_ENV"
set +a
ACTIVE_ENV=$TARGET_ENV
export ACTIVE_ENV
compose --profile migration config --quiet

commit=$(jq -r '.commit' "$TARGET_MANIFEST")
install -m 0600 "$TARGET_MANIFEST" "/opt/storecipe/releases/$commit.json"

# On the very first deployment PostgreSQL does not exist yet. Start only the stateful
# foundation, then back up that initialized empty database before its first migration.
if [[ -z $(compose ps -q postgres) ]]; then
  run_step "initialize stateful services" compose up -d postgres redis-cache redis-broker
  run_step "initial PostgreSQL readiness" wait_for_service_health postgres
fi

run_step "pre-deployment backup" "$ROOT_DIR/scripts/deploy/backup.sh"
run_step "pull immutable images" compose pull "${APP_SERVICES[@]}"
catalog_revision=$(jq -r '.migrations.catalog' "$TARGET_MANIFEST")
ingestion_revision=$(jq -r '.migrations.ingestion' "$TARGET_MANIFEST")
if ! run_step "Catalog migration" compose --profile migration run --rm --no-deps catalog-migrate \
  alembic -c services/catalog/alembic.ini upgrade "$catalog_revision"; then
  migration_failed Catalog
fi
MIGRATIONS_APPLIED=catalog
if ! run_step "Ingestion migration" compose --profile migration run --rm --no-deps ingestion-migrate \
  alembic -c services/ingestion/alembic.ini upgrade "$ingestion_revision"; then
  migration_failed Ingestion
fi
MIGRATIONS_APPLIED=catalog-and-ingestion

STACK_CHANGED=1
run_step "start target release" compose up -d --remove-orphans
run_step "container readiness" wait_for_healthy
run_step "local Host routing" wait_for_local_https
run_step "public TLS and authorization smoke" python3 "$ROOT_DIR/scripts/deploy/smoke_public.py" \
  --origin "$PUBLIC_ORIGIN" --mcp-resource "$MCP_RESOURCE_URL"

install -m 0600 "$TARGET_MANIFEST" "$CURRENT_MANIFEST.next"
mv -f "$CURRENT_MANIFEST.next" "$CURRENT_MANIFEST"
STACK_CHANGED=0
echo "Deployment completed and release $commit is current."
