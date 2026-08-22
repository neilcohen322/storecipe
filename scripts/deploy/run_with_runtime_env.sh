#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [[ $EUID -ne 0 ]]; then
  echo "Scheduled Storecipe operations must run as root" >&2
  exit 1
fi
if [[ $# -ne 1 || ! $1 =~ ^(backup|media-reconcile)$ ]]; then
  echo "usage: storecipe-runtime-operation backup|media-reconcile" >&2
  exit 2
fi

source /etc/storecipe-host.conf
: "${RUNTIME_SECRET_NAME:?RUNTIME_SECRET_NAME is required}"
RUNTIME_ENV=/run/storecipe/runtime.env
install -d -m 0750 -o root -g root /run/storecipe
cleanup() { rm -f "$RUNTIME_ENV"; }
trap cleanup EXIT

gcloud secrets versions access latest --secret "$RUNTIME_SECRET_NAME" > "$RUNTIME_ENV"
chmod 0600 "$RUNTIME_ENV"
for name in GCP_BACKUP_BUCKET POSTGRES_ADMIN_USER POSTGRES_ADMIN_PASSWORD; do
  grep -qE "^$name=.+" "$RUNTIME_ENV" || {
    echo "Runtime bundle is missing required variable: $name" >&2
    exit 1
  }
done

CURRENT_MANIFEST=/opt/storecipe/current/release-manifest.json
[[ -f $CURRENT_MANIFEST ]] || {
  echo "Current release manifest is missing" >&2
  exit 1
}
for mapping in \
  STORECIPE_WEB_IMAGE:.images.web \
  STORECIPE_CATALOG_IMAGE:.images.catalog \
  STORECIPE_INGESTION_IMAGE:.images.ingestion \
  STORECIPE_MCP_IMAGE:.images.mcp; do
  name=${mapping%%:*}
  selector=${mapping#*:}
  printf '%s=%s\n' "$name" "$(jq -r "$selector" "$CURRENT_MANIFEST")" >> "$RUNTIME_ENV"
done

set -a
# The path is fixed, root-owned, and removed on exit.
# shellcheck disable=SC1090
source "$RUNTIME_ENV"
set +a

cd /opt/storecipe/current
case $1 in
  backup)
    scripts/deploy/backup.sh
    ;;
  media-reconcile)
    docker compose --env-file "$RUNTIME_ENV" run --rm --no-deps \
      catalog-api python -m catalog.media_reconciler
    ;;
esac
