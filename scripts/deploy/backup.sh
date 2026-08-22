#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

: "${GCP_BACKUP_BUCKET:?GCP_BACKUP_BUCKET is required}"
: "${POSTGRES_ADMIN_USER:?POSTGRES_ADMIN_USER is required}"
: "${POSTGRES_ADMIN_PASSWORD:?POSTGRES_ADMIN_PASSWORD is required}"

ROOT_DIR=${STORECIPE_ROOT:-/opt/storecipe/current}
COMPOSE_FILE=${STORECIPE_COMPOSE_FILE:-$ROOT_DIR/compose.yaml}
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
BASENAME="storecipe-$TIMESTAMP"
TMP_DIR=$(mktemp -d /var/lib/storecipe/backup.XXXXXX)
trap 'rm -rf "$TMP_DIR"' EXIT

DUMP="$TMP_DIR/$BASENAME.dump"
CHECKSUM="$DUMP.sha256"
MANIFEST="$DUMP.json"

export PGPASSWORD=$POSTGRES_ADMIN_PASSWORD
docker compose -f "$COMPOSE_FILE" exec -T -e PGPASSWORD postgres \
  pg_dump --username "$POSTGRES_ADMIN_USER" --dbname storecipe --format=custom \
  --no-password --file=- > "$DUMP"
unset PGPASSWORD

(cd "$TMP_DIR" && sha256sum "$(basename "$DUMP")" > "$(basename "$CHECKSUM")")
DIGEST=$(cut -d ' ' -f 1 "$CHECKSUM")
printf '{"schema_version":1,"created_at":"%s","sha256":"%s","database":"storecipe"}\n' \
  "$TIMESTAMP" "$DIGEST" > "$MANIFEST"

DAILY="gs://$GCP_BACKUP_BUCKET/daily/$BASENAME"
gcloud storage cp "$DUMP" "$DAILY.dump"
gcloud storage cp "$CHECKSUM" "$DAILY.dump.sha256"
gcloud storage cp "$MANIFEST" "$DAILY.dump.json"

if [[ $(date -u +%u) == 7 ]]; then
  WEEKLY="gs://$GCP_BACKUP_BUCKET/weekly/$BASENAME"
  gcloud storage cp "$DUMP" "$WEEKLY.dump"
  gcloud storage cp "$CHECKSUM" "$WEEKLY.dump.sha256"
  gcloud storage cp "$MANIFEST" "$WEEKLY.dump.json"
fi

prune_prefix() {
  local prefix=$1 keep=$2
  mapfile -t dumps < <(gcloud storage ls "gs://$GCP_BACKUP_BUCKET/$prefix/storecipe-*.dump" 2>/dev/null | sort)
  local remove_count=$((${#dumps[@]} - keep))
  if (( remove_count <= 0 )); then return 0; fi
  for ((index = 0; index < remove_count; index++)); do
    local object=${dumps[$index]}
    [[ $object =~ /storecipe-[0-9]{8}T[0-9]{6}Z\.dump$ ]] || {
      echo "Refusing to prune unexpected backup object" >&2
      return 1
    }
    gcloud storage rm "$object" "$object.sha256" "$object.json"
  done
}

prune_prefix daily 7
prune_prefix weekly 4
echo "Backup completed: daily/$BASENAME (checksum recorded)."
